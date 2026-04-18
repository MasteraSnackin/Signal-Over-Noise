import random

from typing import Literal

RiskBucket = Literal["low", "medium", "high"]


async def get_risk_level(audio_file_path: str) -> RiskBucket:
    return random.choices(
        population=["low", "medium", "high"],
        weights=[0.7, 0.2, 0.1],
        k=1,
    )[0]
