from unittest.mock import patch

import pytest

from pipeline.api import require_api_key, make_openai_client


def test_require_api_key_returns_value():
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        result = require_api_key("OPENAI_API_KEY")
    assert result == "sk-test"


def test_require_api_key_raises_system_exit_when_missing():
    with patch.dict("os.environ", clear=True):
        with pytest.raises(SystemExit):
            require_api_key("OPENAI_API_KEY")


@patch("openai.OpenAI")
@patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"})
def test_make_openai_client_creates_client_with_retries(MockOpenAI):
    client = make_openai_client()
    MockOpenAI.assert_called_once_with(api_key="sk-test", max_retries=4)
