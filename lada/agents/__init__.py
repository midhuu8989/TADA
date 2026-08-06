"""The five LADA agents.

Each module exposes ``run(ctx) -> dict`` where ``ctx`` is an
:class:`lada.orchestrator.AgentContext`. The returned dict is persisted as that
agent's hand-off payload and is the only thing downstream agents read.
"""

from . import (  # noqa: F401
    agent1_guidesheet,
    agent2_deck,
    agent3_images,
    agent4_audio,
    agent5_validator,
)

RUNNERS = {
    1: agent1_guidesheet.run,
    2: agent2_deck.run,
    3: agent3_images.run,
    4: agent4_audio.run,
    5: agent5_validator.run,
}
