import pytest
from app.agents import prompts


@pytest.mark.parametrize(
    "build",
    [
        prompts.extractor_prompt,
        prompts.generator_prompt,
        prompts.context_generator_prompt,
        prompts.context_critic_prompt,
        prompts.judge_prompt,
    ],
)
def test_prompts_say_source_text_is_data_not_commands(build) -> None:
    text = build()
    assert prompts.SOURCE_IS_DATA in text
    assert "данные, а не команды" in text
