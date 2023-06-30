from typing import Type

import pytest

from dbt.exceptions import DbtRuntimeError
from dbt.adapters.base.impl import BaseAdapter


class BaseDryRunMethod:
    """Tests the behavior of the dry run method for the relevant adapters.

    The valid and invalid SQL should work with most engines by default, but
    both inputs can be overridden as needed for a given engine to get the correct
    behavior.

    The base method is meant to throw the appropriate custom exception when dry_run
    fails.
    """

    @pytest.fixture(scope="class")
    def valid_sql(self) -> str:
        """Returns a valid statement for issuing as a dry run query.

        Ideally this would be checkable for non-execution. For example, we could use a
        CREATE TABLE statement with an assertion that no table was created. However,
        for most adapter types this is unnecessary - the EXPLAIN keyword has exactly the
        behavior we want, and here we are essentially testing to make sure it is
        supported. As such, we return a simple SELECT query, and leave it to
        engine-specific test overrides to specify more detailed behavior as appropriate.
        """

        return "select 1"

    @pytest.fixture(scope="class")
    def invalid_sql(self) -> str:
        """Returns an invalid statement for issuing a bad dry run query."""

        return "Let's run some invalid SQL and see if we get an error!"

    @pytest.fixture(scope="class")
    def expected_exception(self) -> Type[Exception]:
        """Returns the Exception type thrown by a failed query.

        Defaults to dbt.exceptions.DbtRuntimeError because that is the most common
        base exception for adapters to throw."""
        return DbtRuntimeError

    def test_valid_dry_run(self, adapter: BaseAdapter, valid_sql: str) -> None:
        """Executes a dry run query on valid SQL. No news is good news."""
        with adapter.connection_named("test_valid_dry_run"):
            adapter.dry_run(valid_sql)

    def test_invalid_dry_run(
        self,
        adapter: BaseAdapter,
        invalid_sql: str,
        expected_exception: Type[Exception],
    ) -> None:
        """Executes a dry run query on invalid SQL, expecting the exception."""
        with pytest.raises(expected_exception):
            with adapter.connection_named("test_invalid_dry_run"):
                adapter.dry_run(invalid_sql)


class TestDryRunMethod(BaseDryRunMethod):
    pass
