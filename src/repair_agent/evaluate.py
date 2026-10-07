"""Independent evaluator. Week 1 checks oracles from ``validate-cases``.

A repair attempt's final patch is evaluated here in week 2, after model access
has ended. Protected output is not returned to the agent.
"""


def evaluate_patch(*_args: object, **_kwargs: object) -> object:
    raise NotImplementedError("Per-attempt evaluation is implemented in week 2.")
