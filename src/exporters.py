#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''Brazilian territorial distribution data exporter

The MIT License (MIT)

Copyright (c) 2013-2016 Paulo Freitas

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
'''

# -- Imports ------------------------------------------------------------------

# Built-in modules

import collections
import csv
import io
import json
import plistlib
import re
import sqlite3
import tempfile

# Dependency modules

import lxml.etree
import phpserialize
import yaml

# -- Modules enhancements -----------------------------------------------------

# yaml


def __represent_odict(dump, tag, mapping, flow_style=None):
    '''Make PyYAML output an OrderedDict.
    Credits: https://gist.github.com/miracle2k/3184458/
    '''
    value = []
    node = yaml.MappingNode(tag, value, flow_style=flow_style)

    if dump.alias_key is not None:
        dump.represented_objects[dump.alias_key] = node

    best_style = True

    if hasattr(mapping, 'items'):
        mapping = mapping.items()

    for item_key, item_value in mapping:
        node_key = dump.represent_data(item_key)
        node_value = dump.represent_data(item_value)

        if not (isinstance(node_key, yaml.ScalarNode) and not node_key.style):
            best_style = False

        if not (isinstance(node_value, yaml.ScalarNode)
                and not node_value.style):
            best_style = False

        value.append((node_key, node_value))

        if flow_style is None:
            if dump.default_flow_style is not None:
                node.flow_style = dump.default_flow_style
            else:
                node.flow_style = best_style

    return node

yaml.SafeDumper.add_representer(
    collections.OrderedDict,
    lambda dumper, value:
        __represent_odict(dumper, u'tag:yaml.org,2002:map', value)
)

# plistlib


def __unsortable_write_dict(self, d):
    self.beginElement('dict')
    items = d.items()

    for key, value in items:
        if not isinstance(key, (str, unicode)):
            raise TypeError('keys must be strings')

        self.simpleElement('key', key)
        self.writeValue(value)

    self.endElement('dict')

plistlib.PlistWriter.writeDict = __unsortable_write_dict

# -- Classes ------------------------------------------------------------------


class BaseExporter(object):
    '''Base exporter class.'''
    def __init__(self, db, minified=False):
        if type(self) == BaseExporter:
            raise Exception('<BaseExporter> must be subclassed.')

        self._db = db
        self._minified = minified

    def __str__(self):
        raise NotImplementedError

    def __toDict__(self, strKeys=False, unicode=False):
        dict_obj = collections.OrderedDict()

        for table_name in self._db._tables:
            if not self._db._data[table_name]:
                continue

            dict_obj[table_name] = collections.OrderedDict()

            for item in self._db._data[table_name]:
                item_obj = collections.OrderedDict()

                for key in self._db._fields[table_name]:
                    item_obj[key] = item[key].decode('utf-8') \
                        if unicode and (type(item[key]) == str) else item[key]

                item_id = str(item_obj['id']) if strKeys else item_obj['id']
                del item_obj['id']
                dict_obj[table_name][item_id] = item_obj

        return dict_obj


class CSV(BaseExporter):
    '''CSV exporter class.'''
    _format = 'csv'
    _extension = '.csv'

    def __str__(self):
        csv_file = io.BytesIO()
        csv_writer = csv.writer(
            csv_file,
            quoting=(csv.QUOTE_NONNUMERIC, csv.QUOTE_MINIMAL)[self._minified],
            lineterminator='\n'
        )

        csv_writer.writerow([col.encode('utf-8') for col in self._db._cols])

        for row in self._db._rows:
            csv_writer.writerow([
                bytes(col) if type(col) == str else col
                for col in filter(None, row)
            ])

        return csv_file.getvalue()


class JSON(BaseExporter):
    '''JSON exporter class.'''
    _format = 'json'
    _extension = '.json'

    def __str__(self):
        json_obj = self.__toDict__()

        if self._minified:
            json_str = json.dumps(json_obj, separators=(',', ':'))
        else:
            json_str = json.dumps(json_obj, indent=2)

        return json_str


class PHP(BaseExporter):
    '''PHP exporter class.'''
    _format = 'php'
    _extension = '.phpd'

    def __str__(self):
        serialize_obj = self.__toDict__()

        return phpserialize.dumps(serialize_obj)


class plist(BaseExporter):
    '''plist exporter class.'''
    _format = 'plist'
    _extension = '.plist'

    def __str__(self):
        plist_str = plistlib.writePlistToString(
            self.__toDict__(strKeys=True, unicode=True)
        )

        return re.sub('[\n\t]+', '', plist_str) if self._minified \
            else plist_str


class SQL(BaseExporter):
    '''SQL exporter class.'''
    _format = 'sql'
    _extension = '.sql'

    def __table(self, table_name, *args):
        return 'CREATE TABLE {} (\n{}\n);\n' \
            .format(table_name, ',\n'.join(args))

    def __column(self, column_name, column_type):
        return '  {} {}'.format(column_name, column_type)

    def __primaryKey(self, table, column):
        pk_name = 'pk_{}'.format(table)

        if self._lazy_constraints:
            stmt = 'ALTER TABLE {}\n  ADD CONSTRAINT {}\n    PRIMARY KEY ({});''' \
                .format(table, pk_name, column)
        else:
            stmt = '  CONSTRAINT {}\n    PRIMARY KEY ({})''' \
                .format(pk_name, column)

        return stmt

    def __foreignKey(self, table, column, foreign_table, foreign_column):
        fk_name = 'fk_{}_{}'.format(table, foreign_table)

        if self._lazy_constraints:
            stmt = 'ALTER TABLE {}\n  ADD CONSTRAINT {}\n    FOREIGN KEY ({})\n      REFERENCES {}({});' \
                .format(table, fk_name, column, foreign_table, foreign_column)
        else:
            stmt = '  CONSTRAINT {}\n    FOREIGN KEY ({})\n      REFERENCES {}({})' \
                .format(fk_name, column, foreign_table, foreign_column)

        return stmt

    def __constraints(sql, *constraints):
        return '\n'.join(constraints) + '\n'

    def __index(self, index_name, table_name, indexed_column):
        return 'CREATE INDEX {} ON {} ({});' \
            .format(index_name, table_name, indexed_column)

    def __indexes(self, *indexes):
        return '\n'.join(indexes) + '\n'

    def __insert(self, table_name, *columns):
        return 'INSERT INTO {} VALUES ({});' \
            .format(table_name, ', '.join(columns))

    def __insertField(self, field_name, field_type):
        repl_field = '{' + field_name + '}'

        return repr(repl_field) if field_type == str else repl_field

    def __quote(self, value):
        return value.replace("'", self._escape_char)

    def __init__(self, db, minified, dialect='standard'):
        super(SQL, self).__init__(db, minified)

        # Standard settings
        self._lazy_constraints = True
        self._create_indexes = True
        self._bigint_type = 'BIGINT'
        self._escape_char = "\\'"

        # Handle dialect settings
        if dialect == 'sqlite':
            self._lazy_constraints = False
            self._escape_char = "''"

        # SQL data
        self._tables = {
            'uf': [
                self.__column('id', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(32) NOT NULL')
            ],
            'mesorregiao': [
                self.__column('id', 'SMALLINT NOT NULL'),
                self.__column('id_uf', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(64) NOT NULL')
            ],
            'microrregiao': [
                self.__column('id', 'INTEGER NOT NULL'),
                self.__column('id_mesorregiao', 'SMALLINT NOT NULL'),
                self.__column('id_uf', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(64) NOT NULL')
            ],
            'municipio': [
                self.__column('id', 'INTEGER NOT NULL'),
                self.__column('id_microrregiao', 'INTEGER NOT NULL'),
                self.__column('id_mesorregiao', 'SMALLINT NOT NULL'),
                self.__column('id_uf', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(64) NOT NULL')
            ],
            'distrito': [
                self.__column('id', 'INTEGER NOT NULL'),
                self.__column('id_municipio', 'INTEGER NOT NULL'),
                self.__column('id_microrregiao', 'INTEGER NOT NULL'),
                self.__column('id_mesorregiao', 'SMALLINT NOT NULL'),
                self.__column('id_uf', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(64) NOT NULL')
            ],
            'subdistrito': [
                self.__column('id', '{} NOT NULL'.format(self._bigint_type)),
                self.__column('id_distrito', 'INTEGER NOT NULL'),
                self.__column('id_municipio', 'INTEGER NOT NULL'),
                self.__column('id_microrregiao', 'INTEGER NOT NULL'),
                self.__column('id_mesorregiao', 'SMALLINT NOT NULL'),
                self.__column('id_uf', 'SMALLINT NOT NULL'),
                self.__column('nome', 'VARCHAR(64) NOT NULL')
            ]
        }
        self._constraints = {
            'uf': [
                self.__primaryKey('uf', 'id')
            ],
            'mesorregiao': [
                self.__primaryKey('mesorregiao', 'id'),
                self.__foreignKey('mesorregiao', 'id_uf', 'uf', 'id')
            ],
            'microrregiao': [
                self.__primaryKey('microrregiao', 'id'),
                self.__foreignKey(
                    'microrregiao', 'id_mesorregiao', 'mesorregiao', 'id'
                ),
                self.__foreignKey('microrregiao', 'id_uf', 'uf', 'id')
            ],
            'municipio': [
                self.__primaryKey('municipio', 'id'),
                self.__foreignKey(
                    'municipio', 'id_microrregiao', 'microrregiao', 'id'
                ),
                self.__foreignKey(
                    'municipio', 'id_mesorregiao', 'mesorregiao', 'id'
                ),
                self.__foreignKey('municipio', 'id_uf', 'uf', 'id')
            ],
            'distrito': [
                self.__primaryKey('distrito', 'id'),
                self.__foreignKey(
                    'distrito', 'id_municipio', 'municipio', 'id'
                ),
                self.__foreignKey(
                    'distrito', 'id_microrregiao', 'microrregiao', 'id'
                ),
                self.__foreignKey(
                    'distrito', 'id_mesorregiao', 'mesorregiao', 'id'
                ),
                self.__foreignKey('distrito', 'id_uf', 'uf', 'id')
            ],
            'subdistrito': [
                self.__primaryKey('subdistrito', 'id'),
                self.__foreignKey(
                    'subdistrito', 'id_distrito', 'distrito', 'id'
                ),
                self.__foreignKey(
                    'subdistrito', 'id_municipio', 'municipio', 'id'
                ),
                self.__foreignKey(
                    'subdistrito', 'id_microrregiao', 'microrregiao', 'id'
                ),
                self.__foreignKey(
                    'subdistrito', 'id_mesorregiao', 'mesorregiao', 'id'
                ),
                self.__foreignKey('subdistrito', 'id_uf', 'uf', 'id')
            ]
        }
        self._indexes = {
            'mesorregiao': [
                self.__index('fk_mesorregiao_uf', 'mesorregiao', 'id_uf')
            ],
            'microrregiao': [
                self.__index(
                    'fk_microrregiao_mesorregiao',
                    'microrregiao',
                    'id_mesorregiao'
                ),
                self.__index('fk_microrregiao_uf', 'microrregiao', 'id_uf')
            ],
            'municipio': [
                self.__index(
                    'fk_municipio_microrregiao', 'municipio', 'id_microrregiao'
                ),
                self.__index(
                    'fk_municipio_mesorregiao', 'municipio', 'id_mesorregiao'
                ),
                self.__index('fk_municipio_uf', 'municipio', 'id_uf')
            ],
            'distrito': [
                self.__index(
                    'fk_distrito_municipio', 'distrito', 'id_municipio'
                ),
                self.__index(
                    'fk_distrito_microrregiao', 'distrito', 'id_microrregiao'
                ),
                self.__index(
                    'fk_distrito_mesorregiao', 'distrito', 'id_mesorregiao'
                ),
                self.__index('fk_distrito_uf', 'distrito', 'id_uf')
            ],
            'subdistrito': [
                self.__index(
                    'fk_subdistrito_distrito', 'subdistrito', 'id_distrito'
                ),
                self.__index(
                    'fk_subdistrito_municipio', 'subdistrito', 'id_municipio'
                ),
                self.__index(
                    'fk_subdistrito_microrregiao',
                    'subdistrito',
                    'id_microrregiao'
                ),
                self.__index(
                    'fk_subdistrito_mesorregiao',
                    'subdistrito',
                    'id_mesorregiao'
                ),
                self.__index('fk_subdistrito_uf', 'subdistrito', 'id_uf')
            ]
        }
        self._inserts = {
            'uf': [
                self.__insertField('id', int),
                self.__insertField('nome', str)
            ],
            'mesorregiao': [
                self.__insertField('id', int),
                self.__insertField('id_uf', int),
                self.__insertField('nome', str)
            ],
            'microrregiao': [
                self.__insertField('id', int),
                self.__insertField('id_mesorregiao', int),
                self.__insertField('id_uf', int),
                self.__insertField('nome', str)
            ],
            'municipio': [
                self.__insertField('id', int),
                self.__insertField('id_microrregiao', int),
                self.__insertField('id_mesorregiao', int),
                self.__insertField('id_uf', int),
                self.__insertField('nome', str)
            ],
            'distrito': [
                self.__insertField('id', int),
                self.__insertField('id_municipio', int),
                self.__insertField('id_microrregiao', int),
                self.__insertField('id_mesorregiao', int),
                self.__insertField('id_uf', int),
                self.__insertField('nome', str)
            ],
            'subdistrito': [
                self.__insertField('id', int),
                self.__insertField('id_distrito', int),
                self.__insertField('id_municipio', int),
                self.__insertField('id_microrregiao', int),
                self.__insertField('id_mesorregiao', int),
                self.__insertField('id_uf', int),
                self.__insertField('nome', str)
            ]
        }

    def __str__(self):
        sql = ''

        for table_name in self._db._tables:
            if not self._db._data[table_name]:
                continue

            if not self._minified:
                sql += '''
--
-- Structure for table "{}"
--
'''.format(table_name)

            cols = self._tables[table_name] if self._lazy_constraints \
                else self._tables[table_name] + self._constraints[table_name]

            sql += self.__table(table_name, *cols)

            if not self._minified:
                sql += '''
--
-- Data for table "{}"
--
'''.format(table_name)

            for item in self._db._data[table_name]:
                data = collections.OrderedDict()

                for key in self._db._fields[table_name]:
                    data[key] = item[key] if type(item[key]) == int \
                        else self.__quote(item[key])

                sql += self.__insert(table_name, *self._inserts[table_name]) \
                    .strip().format(**data) + '\n'

            if self._lazy_constraints:
                if not self._minified:
                    sql += '''
--
-- Constraints for table "{}"
--
'''.format(table_name)

                sql += self.__constraints(*self._constraints[table_name])

            if self._create_indexes:
                if table_name in self._indexes:
                    if not self._minified:
                        sql += '''
--
-- Indexes for table "{}"
--
'''.format(table_name)

                    sql += self.__indexes(*self._indexes[table_name])

        sql = sql.strip()

        if self._minified:
            sql = re.sub('(?<=[;(])\s+', '', sql)
            sql = re.sub(',\s(?=\')|,\s+', ',', sql)
            sql = re.sub('\s+', ' ', sql)
            sql = re.sub('(?<=\W)\s(?=\W)', '', sql)
            sql = re.sub('(?<=\w)\s(?=\))', '', sql)
            sql = re.sub("\s\((?=(?:(?:[^']*'){2})*[^']*$)", '(', sql)

        return sql


class SQLite3(BaseExporter):
    '''SQLite3 exporter class.'''
    _format = 'sqlite3'
    _extension = '.sqlite3'

    def __str__(self):
        sql_str = str(SQL(self._db, self._minified, dialect='sqlite'))

        with tempfile.NamedTemporaryFile() as sqlite_file:
            with sqlite3.connect(sqlite_file.name) as sqlite_con:
                sqlite_cursor = sqlite_con.cursor()
                sqlite_cursor.executescript('BEGIN; {} COMMIT'.format(sql_str))

            sqlite_data = open(sqlite_file.name, 'rb').read()

        return sqlite_data


class XML(BaseExporter):
    '''XML exporter class.'''
    _format = 'xml'
    _extension = '.xml'

    def __str__(self):
        database = lxml.etree.Element('database', name=self._db._name)

        for table_name in self._db._tables:
            if not self._db._data[table_name]:
                continue

            if not self._minified:
                database.append(
                    lxml.etree.Comment(' Table {} '.format(table_name))
                )

            table = lxml.etree.SubElement(database, 'table', name=table_name)

            for item in self._db._data[table_name]:
                row = lxml.etree.SubElement(table, 'row')

                for field_name in self._db._fields[table_name]:
                    lxml.etree.SubElement(row, 'field', name=field_name).text =\
                        str(item[field_name]).decode('utf-8')

        return lxml.etree.tostring(database,
                                   pretty_print=not self._minified,
                                   xml_declaration=True,
                                   encoding='utf-8')


class YAML(BaseExporter):
    '''YAML exporter class.'''
    _format = 'yaml'
    _extension = '.yaml'

    def __str__(self):
        yaml_obj = self.__toDict__()
        yaml_opts = {'default_flow_style': False}

        if self._minified:
            yaml_opts = {'default_flow_style': True, 'width': 2e6, 'indent': 0}

        yaml_str = yaml.safe_dump(yaml_obj, **yaml_opts)

        return yaml_str.replace('}, ', '},') if self._minified else yaml_str
