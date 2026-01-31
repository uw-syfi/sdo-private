"""Tests for DSPy field mappings."""

import pytest
from pathlib import Path

from app_operator.dspy_integration.field_mappings import (
    convert_type,
    map_kwargs_to_fields,
    get_output_field_name,
    get_all_output_fields,
)


class TestConvertType:
    """Tests for type conversion."""

    def test_convert_path_to_str(self):
        """Path objects should be converted to strings."""
        result = convert_type(Path('/repo'))
        assert result == '/repo'
        assert isinstance(result, str)

    def test_convert_string_unchanged(self):
        """Strings should remain unchanged."""
        result = convert_type('hello')
        assert result == 'hello'

    def test_convert_int_unchanged(self):
        """Integers should remain unchanged."""
        result = convert_type(42)
        assert result == 42

    def test_convert_bool_unchanged(self):
        """Booleans should remain unchanged."""
        result = convert_type(True)
        assert result is True


class TestMapKwargsToFields:
    """Tests for kwargs to fields mapping."""

    def test_auto_mapping(self):
        """Fields with matching names should auto-map."""
        kwargs = {
            'repo_path': Path('/repo'),
            'attempt': 1,
            'max_attempts': 3,
        }
        result = map_kwargs_to_fields('deployer_fix_error', kwargs)

        assert result['repo_path'] == '/repo'  # Path converted to str
        assert result['attempt'] == 1
        assert result['max_attempts'] == 3

    def test_explicit_mapping_error_log(self):
        """error_log should map to error_context for deployer_fix_error."""
        kwargs = {
            'repo_path': Path('/repo'),
            'error_log': 'Error occurred',
            'attempt': 1,
        }
        result = map_kwargs_to_fields('deployer_fix_error', kwargs)

        assert 'error_context' in result
        assert result['error_context'] == 'Error occurred'
        assert 'error_log' not in result  # Original key not in result

    def test_explicit_mapping_deployment_log(self):
        """log should map to deployment_log for deployer_summarize."""
        kwargs = {
            'log': 'Deployment output...',
            'health_check_result': 'OK',
            'success': True,
        }
        result = map_kwargs_to_fields('deployer_summarize', kwargs)

        assert 'deployment_log' in result
        assert result['deployment_log'] == 'Deployment output...'
        assert 'log' not in result

    def test_mixed_mapping(self):
        """Should handle both explicit and auto mappings."""
        kwargs = {
            'repo_path': Path('/repo'),
            'error_log': 'Error',  # Explicit mapping
            'attempt': 2,  # Auto mapping
            'deploy_script': '/repo/.sds/deploy.sh',  # Auto mapping
        }
        result = map_kwargs_to_fields('deployer_fix_error', kwargs)

        assert result['repo_path'] == '/repo'
        assert result['error_context'] == 'Error'
        assert result['attempt'] == 2
        assert result['deploy_script'] == '/repo/.sds/deploy.sh'

    def test_no_explicit_mappings(self):
        """Prompts without explicit mappings should auto-map everything."""
        kwargs = {
            'repo_path': Path('/repo'),
            'agent_name': 'deployer',
        }
        result = map_kwargs_to_fields('deployer_system', kwargs)

        assert result['repo_path'] == '/repo'
        assert result['agent_name'] == 'deployer'


class TestGetOutputFieldName:
    """Tests for output field name retrieval."""

    def test_deployer_prompts(self):
        """Test output fields for deployer prompts."""
        assert get_output_field_name('deployer_system') == 'system_prompt'
        assert get_output_field_name('deployer_generate_script') == 'deployment_script'
        assert get_output_field_name('deployer_fix_error') == 'fix_summary'
        assert get_output_field_name('deployer_summarize') == 'summary'

    def test_code_analyzer_prompts(self):
        """Test output fields for code analyzer prompts."""
        assert get_output_field_name('code_analyzer_system') == 'system_prompt'
        assert get_output_field_name('code_analyzer_user') == 'code_analysis'

    def test_monitor_prompts(self):
        """Test output fields for monitor prompts."""
        assert get_output_field_name('monitor_analyze_health') == 'health_status'

    def test_agentflow_prompts(self):
        """Test output fields for agentflow prompts."""
        assert get_output_field_name('agentflow_system') == 'system_prompt'
        assert get_output_field_name('agentflow_user') == 'script_code'
        assert get_output_field_name('agentflow_repair') == 'repaired_response'

    def test_invalid_prompt_name(self):
        """Should raise KeyError for unknown prompt."""
        with pytest.raises(KeyError) as exc_info:
            get_output_field_name('unknown_prompt')

        assert 'No output field mapping' in str(exc_info.value)
        assert 'unknown_prompt' in str(exc_info.value)


class TestGetAllOutputFields:
    """Tests for multi-output field retrieval."""

    def test_single_output_prompt(self):
        """Single-output prompts should return dict with primary field."""
        result = get_all_output_fields('deployer_fix_error')
        assert result == {'fix_summary': 'fix_summary'}

    def test_deployer_generate_script(self):
        """deployer_generate_script has two outputs."""
        result = get_all_output_fields('deployer_generate_script')
        assert result == {
            'deployment_script': 'deployment_script',
            'health_check_script': 'health_check_script',
        }

    def test_code_analyzer_user(self):
        """code_analyzer_user has two outputs."""
        result = get_all_output_fields('code_analyzer_user')
        assert result == {
            'code_analysis': 'code_analysis',
            'deployment_issues': 'deployment_issues',
        }

    def test_monitor_analyze_health(self):
        """monitor_analyze_health has two outputs."""
        result = get_all_output_fields('monitor_analyze_health')
        assert result == {
            'health_status': 'health_status',
            'is_healthy': 'is_healthy',
        }

    def test_agentflow_user(self):
        """agentflow_user has three outputs."""
        result = get_all_output_fields('agentflow_user')
        assert result == {
            'script_code': 'script_code',
            'status': 'status',
            'questions': 'questions',
        }
