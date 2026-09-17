"""AskUserQuestion 的内部函数: 入参 -> HumanPrompt, 回答 -> 回给模型的文字."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from forgecli.human_interaction.prompt import (
    HumanPrompt,
    PromptAnswer,
    PromptChoice,
    PromptKind,
)

__all__ = ["answer_text", "build_prompt", "choices_of"]

MAX_OPTIONS = 5


def choices_of(raw: object) -> tuple[PromptChoice, ...]:
    items: Sequence[object] = raw if isinstance(raw, list) else ()
    choices: list[PromptChoice] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        choices.append(
            PromptChoice(
                value=str(item.get("value", "")),
                label=str(item.get("label", "")),
                detail=str(item.get("detail", "")),
            )
        )
    return tuple(choices)


def build_prompt(prompt_id: str, arguments: Mapping[str, object]) -> HumanPrompt:
    return HumanPrompt(
        prompt_id=prompt_id,
        kind=PromptKind.QUESTION,
        title=str(arguments.get("question", "")),
        body=str(arguments.get("description", "")),
        choices=choices_of(arguments.get("options")),
        free_text=True,
        selection_mode=str(arguments.get("selection_mode", "single")),
        recommended_option_id=str(arguments.get("recommended_option_id", "")),
        allow_skip=True,
    )


def answer_text(arguments: Mapping[str, object], answer: PromptAnswer) -> str:
    """回给模型的那一段. 点了选项时回那一项的 label: 它是模型给人读的那一句."""
    if answer.skipped:
        return (
            "The user skipped this question without answering. Do not treat the "
            "recommended option as accepted; continue with the information you have "
            "and state your assumptions."
        )
    values = answer.selected_values
    if not values and answer.choice:
        values = (answer.choice,)
    labels: list[str] = []
    for choice in choices_of(arguments.get("options")):
        if choice.value in values:
            labels.append(choice.label)
    if answer.text:
        labels.append(answer.text)
    return "\n".join(labels)
