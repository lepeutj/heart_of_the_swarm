import pytest

from heart_of_the_swarm.tools.builtin import calculator


def test_calculator_handles_arithmetic() -> None:
    assert calculator.invoke({"expression": "(12.5 * 4) - 3"}) == "47.0"


@pytest.mark.parametrize("expression", ["__import__('os')", "2 ** 101", "[1, 2]"])
def test_calculator_rejects_unsafe_or_excessive_expressions(expression: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        calculator.invoke({"expression": expression})
