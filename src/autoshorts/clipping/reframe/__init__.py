"""Рефрейм 16:9 → 9:16 для клипа (docs/ARCHITECTURE.md §6.3.6, стадия `clip_analyze`).

    plan = plan_reframe("proxy_540p.mp4", 84.1, 122.0, src_w=1920, src_h=1080)
    plan.pieces(a, b)                 # куски источника по шотам со стратегией track|center|fit_blur
    plan.sendcmd_for(a, b, target="crop@c0")   # строки файла crop_000.cmd

Модули: `shots` (PySceneDetect AdaptiveDetector), `cameraman` (SmoothedCameraman),
`strategy` (выбор стратегии на шот, план). Лица — `providers.faces` (YuNet).
Импорт пакета не тянет cv2/scenedetect: они подгружаются при анализе.
"""

from autoshorts.clipping.reframe.cameraman import CameraConfig, SmoothedCameraman
from autoshorts.clipping.reframe.strategy import (
    REFRAME_MODES,
    CropGeometry,
    ReframeMode,
    ReframePlan,
    ShotPlan,
    StrategyConfig,
    build_plan,
    choose_strategy,
    plan_reframe,
    static_plan,
)

__all__ = [
    "REFRAME_MODES",
    "CameraConfig",
    "CropGeometry",
    "ReframeMode",
    "ReframePlan",
    "ShotPlan",
    "SmoothedCameraman",
    "StrategyConfig",
    "build_plan",
    "choose_strategy",
    "plan_reframe",
    "static_plan",
]
