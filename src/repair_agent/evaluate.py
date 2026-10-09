"""Independent evaluator. ``validate-cases`` checks oracles.

Per-attempt evaluation of a final patch, after model access has ended, is not
implemented yet. Protected output is not returned to the agent.
"""


def evaluate_patch(*_args: object, **_kwargs: object) -> object:
    raise NotImplementedError("Per-attempt evaluation is not implemented yet.")
