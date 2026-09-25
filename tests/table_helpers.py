"""Read a QTableWidget's cells back as text, for the stats view and report
view tests."""

from __future__ import annotations


def table_rows(table):
    """A QTableWidget as {row header: [cell texts]}."""
    return {
        table.verticalHeaderItem(row).text(): [
            table.item(row, column).text() for column in range(table.columnCount())
        ]
        for row in range(table.rowCount())
    }


def column_headers(table):
    return [
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ]


def column(table, header):
    return column_headers(table).index(header)


def row(table, key_column, *key):
    """The row whose cells under `key_column` (and the columns after it,
    for a multi-part key) read `key`, as {column header: cell text}."""
    first = column(table, key_column)
    index = next(
        index
        for index in range(table.rowCount())
        if tuple(table.item(index, first + i).text() for i in range(len(key))) == key
    )
    return {
        header: table.item(index, position).text()
        for position, header in enumerate(column_headers(table))
    }


def all_cells(table):
    """Every header and cell, for comparing two tables wholesale."""
    return (
        column_headers(table),
        [
            table.verticalHeaderItem(index).text()
            if table.verticalHeaderItem(index) is not None
            else None
            for index in range(table.rowCount())
        ],
        [
            [
                table.item(index, position).text()
                for position in range(table.columnCount())
            ]
            for index in range(table.rowCount())
        ],
    )
