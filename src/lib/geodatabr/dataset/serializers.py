#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2013-2018 Paulo Freitas
# MIT License (see LICENSE file)
"""
Dataset serialization module.

This module provides the serializers classes used to export the dataset.
"""
# Imports

# Built-in dependencies

from typing import Any

# Package dependencies

from geodatabr.core.helpers.decorators import cachedmethod
from geodatabr.core.types import List, OrderedMap
from geodatabr.core.i18n import _
from geodatabr.dataset.schema import Entities
from geodatabr.dataset.repositories import StateRepository

# Classes


class BaseSerializer(object):
    """Abstract serializer class."""

    @property
    @cachedmethod()
    def __records__(self) -> OrderedMap:
        """
        Retrieves the dataset records.

        Returns:
            The dataset records
        """
        records = OrderedMap(states=StateRepository().loadAll())

        for _entity in Entities:
            entity = _entity.__table__.name

            if entity not in records:
                records[entity] = List([item
                                        for state in records.states
                                        for item in getattr(state, entity)])

        return records

    def __init__(self, **options):
        """
        Setup the serializer.

        Args:
            **options: The serialization options
        """
        self._options = OrderedMap(options)

    def serialize(self) -> Any:
        """
        Abstract serialization method.
        """
        raise NotImplementedError


class Serializer(BaseSerializer):
    """Default serialization implementation."""

    def __init__(self,
                 localize: bool = True,
                 forceStr: bool = False):
        """
        Setup the serializer.

        Args:
            localize: Whether or not it should localize mapping keys
            forceStr: Whether or not it should coerce mapping values to string
        """
        super().__init__(localize=localize,
                         forceStr=forceStr)

    @cachedmethod()
    def serialize(self) -> OrderedMap:
        """
        Serializes the dataset records.

        Returns:
            The serialized dataset records mapping
        """
        records = OrderedMap()

        for entity in Entities:
            table = str(entity.__table__.name)
            _records = self.__records__[table]

            if not _records:
                continue

            if self._options.localize:
                table = _(table)

            records[table] = List()

            for _record in _records:
                _record = _record.serialize()
                record = OrderedMap()

                for column in entity.__table__.columns:
                    column = str(column.name)
                    value = _record[column]

                    if self._options.localize:
                        column = _(column)

                    record[column] = str(value) if self._options.forceStr else value

                records[table].append(record)

        return records


class FlattenedSerializer(BaseSerializer):
    """Flattened serialization implementation."""

    @cachedmethod()
    def serialize(self) -> List:
        """
        Serializes the dataset records.

        Returns:
            The serialized dataset records list
        """
        records = List()

        for entity in Entities:
            for record in self.__records__[entity.__table__.name]:
                records.append(OrderedMap(
                    [(_(key), value)
                     for key, value in record.serialize(flatten=True).items()]))

        records.sort(key=lambda record: (record.get('state_id') or 0,
                                         record.get('mesoregion_id') or 0,
                                         record.get('microregion_id') or 0,
                                         record.get('municipality_id') or 0,
                                         record.get('district_id') or 0,
                                         record.get('subdistrict_id') or 0))

        return records
