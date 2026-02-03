"""
Tests for SQL Validator Module
"""

import pytest
from sql_validator import SQLValidator, ValidationResult, ValidationSeverity, validate_sql, is_safe_sql


class TestSQLValidator:
    """Test cases for SQLValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a default validator instance."""
        return SQLValidator()

    @pytest.fixture
    def strict_validator(self):
        """Create a strict validator instance."""
        return SQLValidator(enable_strict_mode=True)

    @pytest.fixture
    def lenient_validator(self):
        """Create a lenient validator instance."""
        return SQLValidator(enable_strict_mode=False)

    # ==========================================================================
    # Valid Query Tests
    # ==========================================================================

    def test_valid_simple_select(self, validator):
        """Test that a simple SELECT query is valid."""
        sql = "SELECT * FROM company_financials LIMIT 10"
        result = validator.validate(sql)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_valid_select_with_where(self, validator):
        """Test SELECT with WHERE clause."""
        sql = """
        SELECT ticker, company_name, roe_percent
        FROM company_financials
        WHERE is_latest = TRUE AND is_annual = TRUE
        ORDER BY roe_percent DESC
        LIMIT 20
        """
        result = validator.validate(sql)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_valid_select_with_joins(self, validator):
        """Test SELECT with JOIN clauses."""
        sql = """
        SELECT c.ticker, c.company_name, s.sector_name
        FROM companies c
        JOIN sectors s ON c.sector_id = s.sector_id
        LIMIT 50
        """
        result = validator.validate(sql)

        assert result.is_valid

    def test_valid_aggregate_query(self, validator):
        """Test aggregate queries."""
        sql = """
        SELECT sector, COUNT(*) as count, AVG(roe_percent) as avg_roe
        FROM company_financials
        WHERE is_latest = TRUE
        GROUP BY sector
        ORDER BY avg_roe DESC
        LIMIT 20
        """
        result = validator.validate(sql)

        assert result.is_valid

    def test_valid_with_clause(self, validator):
        """Test WITH (CTE) queries."""
        sql = """
        WITH top_companies AS (
            SELECT ticker, roe_percent
            FROM company_financials
            WHERE is_latest = TRUE
        )
        SELECT * FROM top_companies
        ORDER BY roe_percent DESC
        LIMIT 10
        """
        result = validator.validate(sql)

        assert result.is_valid

    # ==========================================================================
    # Blocked Keyword Tests
    # ==========================================================================

    def test_blocks_drop_table(self, validator):
        """Test that DROP TABLE is blocked."""
        sql = "DROP TABLE companies"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("DROP" in e for e in result.errors)

    def test_blocks_delete(self, validator):
        """Test that DELETE is blocked."""
        sql = "DELETE FROM companies WHERE ticker = '1234'"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("DELETE" in e for e in result.errors)

    def test_blocks_insert(self, validator):
        """Test that INSERT is blocked."""
        sql = "INSERT INTO companies (ticker) VALUES ('1234')"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("INSERT" in e for e in result.errors)

    def test_blocks_update(self, validator):
        """Test that UPDATE is blocked."""
        sql = "UPDATE companies SET company_name = 'test' WHERE ticker = '1234'"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("UPDATE" in e for e in result.errors)

    def test_blocks_truncate(self, validator):
        """Test that TRUNCATE is blocked."""
        sql = "TRUNCATE TABLE companies"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("TRUNCATE" in e for e in result.errors)

    def test_blocks_alter(self, validator):
        """Test that ALTER is blocked."""
        sql = "ALTER TABLE companies ADD COLUMN test VARCHAR(10)"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("ALTER" in e for e in result.errors)

    def test_blocks_create(self, validator):
        """Test that CREATE is blocked."""
        sql = "CREATE TABLE test (id INT)"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("CREATE" in e for e in result.errors)

    def test_blocks_grant(self, validator):
        """Test that GRANT is blocked."""
        sql = "GRANT SELECT ON companies TO public"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("GRANT" in e for e in result.errors)

    # ==========================================================================
    # SQL Injection Tests
    # ==========================================================================

    def test_blocks_statement_chaining_drop(self, validator):
        """Test that statement chaining with DROP is blocked."""
        sql = "SELECT * FROM companies; DROP TABLE companies"
        result = validator.validate(sql)

        assert not result.is_valid

    def test_blocks_statement_chaining_delete(self, validator):
        """Test that statement chaining with DELETE is blocked."""
        sql = "SELECT * FROM companies; DELETE FROM companies"
        result = validator.validate(sql)

        assert not result.is_valid

    def test_blocks_union_injection(self, validator):
        """Test that UNION injection patterns are blocked."""
        sql = "SELECT * FROM companies WHERE ticker = '1' UNION SELECT * FROM information_schema.tables"
        result = validator.validate(sql)

        assert not result.is_valid

    def test_blocks_comment_injection(self, validator):
        """Test that comment-based injection is blocked."""
        sql = "SELECT * FROM companies WHERE ticker = '1' --"
        result = validator.validate(sql)

        assert not result.is_valid

    # ==========================================================================
    # System Table Access Tests
    # ==========================================================================

    def test_blocks_information_schema(self, validator):
        """Test that information_schema access is blocked."""
        sql = "SELECT * FROM information_schema.tables"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("information_schema" in e.lower() for e in result.errors)

    def test_blocks_pg_catalog(self, validator):
        """Test that pg_catalog access is blocked."""
        sql = "SELECT * FROM pg_catalog.pg_tables"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("pg_catalog" in e.lower() for e in result.errors)

    # ==========================================================================
    # Table Validation Tests
    # ==========================================================================

    def test_allows_known_tables(self, validator):
        """Test that known tables are allowed."""
        sql = "SELECT * FROM company_financials LIMIT 10"
        result = validator.validate(sql)

        assert result.is_valid

    def test_strict_mode_blocks_unknown_tables(self, strict_validator):
        """Test that unknown tables are blocked in strict mode."""
        sql = "SELECT * FROM unknown_table LIMIT 10"
        result = strict_validator.validate(sql)

        assert not result.is_valid
        assert any("not in the allowed list" in e for e in result.errors)

    def test_lenient_mode_warns_unknown_tables(self, lenient_validator):
        """Test that unknown tables generate warnings in lenient mode."""
        sql = "SELECT * FROM unknown_table LIMIT 10"
        result = lenient_validator.validate(sql)

        # Should still be valid but with warnings
        assert result.is_valid
        assert any("not in the allowed list" in w for w in result.warnings)

    # ==========================================================================
    # LIMIT Handling Tests
    # ==========================================================================

    def test_adds_limit_when_missing(self, validator):
        """Test that LIMIT is added when missing."""
        sql = "SELECT * FROM company_financials"
        result = validator.validate(sql)

        assert result.is_valid
        assert result.modified
        assert "LIMIT" in result.sql.upper()

    def test_preserves_existing_limit(self, validator):
        """Test that existing LIMIT is preserved."""
        sql = "SELECT * FROM company_financials LIMIT 5"
        result = validator.validate(sql)

        assert result.is_valid
        assert "LIMIT 5" in result.sql

    def test_custom_max_rows(self):
        """Test custom max_rows setting."""
        validator = SQLValidator(max_rows=50)
        sql = "SELECT * FROM company_financials"
        result = validator.validate(sql)

        assert "LIMIT 50" in result.sql

    # ==========================================================================
    # Markdown Cleaning Tests
    # ==========================================================================

    def test_removes_sql_code_blocks(self, validator):
        """Test that SQL code blocks are removed."""
        sql = "```sql\nSELECT * FROM company_financials LIMIT 10\n```"
        result = validator.validate(sql)

        assert result.is_valid
        assert result.modified
        assert "```" not in result.sql

    def test_removes_generic_code_blocks(self, validator):
        """Test that generic code blocks are removed."""
        sql = "```\nSELECT * FROM company_financials LIMIT 10\n```"
        result = validator.validate(sql)

        assert result.is_valid
        assert result.modified

    # ==========================================================================
    # Edge Cases
    # ==========================================================================

    def test_empty_sql(self, validator):
        """Test that empty SQL is rejected."""
        result = validator.validate("")

        assert not result.is_valid
        assert any("Empty" in e for e in result.errors)

    def test_whitespace_only_sql(self, validator):
        """Test that whitespace-only SQL is rejected."""
        result = validator.validate("   \n\t  ")

        assert not result.is_valid

    def test_non_select_query(self, validator):
        """Test that non-SELECT queries are rejected."""
        sql = "SHOW TABLES"
        result = validator.validate(sql)

        assert not result.is_valid
        assert any("SELECT" in e for e in result.errors)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_validate_sql_function(self):
        """Test the validate_sql convenience function."""
        result = validate_sql("SELECT * FROM company_financials LIMIT 10")

        assert result.is_valid
        assert isinstance(result, ValidationResult)

    def test_is_safe_sql_function_safe(self):
        """Test is_safe_sql with safe query."""
        assert is_safe_sql("SELECT * FROM company_financials LIMIT 10")

    def test_is_safe_sql_function_unsafe(self):
        """Test is_safe_sql with unsafe query."""
        assert not is_safe_sql("DROP TABLE companies")


class TestValidationResult:
    """Test ValidationResult class."""

    def test_errors_property(self):
        """Test errors property filters correctly."""
        result = ValidationResult(
            is_valid=False,
            sql="test",
            issues=[
                (ValidationSeverity.ERROR, "Error 1"),
                (ValidationSeverity.WARNING, "Warning 1"),
                (ValidationSeverity.ERROR, "Error 2"),
            ]
        )

        assert len(result.errors) == 2
        assert "Error 1" in result.errors
        assert "Error 2" in result.errors

    def test_warnings_property(self):
        """Test warnings property filters correctly."""
        result = ValidationResult(
            is_valid=False,
            sql="test",
            issues=[
                (ValidationSeverity.ERROR, "Error 1"),
                (ValidationSeverity.WARNING, "Warning 1"),
                (ValidationSeverity.WARNING, "Warning 2"),
            ]
        )

        assert len(result.warnings) == 2
        assert "Warning 1" in result.warnings
        assert "Warning 2" in result.warnings
