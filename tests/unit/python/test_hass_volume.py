from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import lib.volume_adapters.hass as hass_mod


def _mock_cfg(step: int = 3, mlgw_step_multiplier: float = 2.0):
    values = {
        ("volume", "step"): step,
        ("volume", "mlgw_step_multiplier"): mlgw_step_multiplier,
    }

    def _cfg(*keys, default=None):
        return values.get(keys, default)

    return _cfg


@pytest.mark.asyncio
async def test_apply_volume_scales_mlgw_steps(monkeypatch):
    monkeypatch.setattr(hass_mod, "cfg", _mock_cfg(step=3, mlgw_step_multiplier=2.0))
    adapter = hass_mod.HassVolume(100, MagicMock(), default_vol=30)
    adapter._get_active_target = AsyncMock(return_value="media_player.room")
    adapter._send_mlgw_steps = AsyncMock(return_value=True)
    adapter._send_ha_steps = AsyncMock(return_value=True)

    await adapter._apply_volume(33)

    adapter._send_mlgw_steps.assert_awaited_once_with("media_player.room", 2, 3.0)
    adapter._send_ha_steps.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_volume_keeps_base_steps_for_ha_fallback(monkeypatch):
    monkeypatch.setattr(hass_mod, "cfg", _mock_cfg(step=3, mlgw_step_multiplier=2.0))
    adapter = hass_mod.HassVolume(100, MagicMock(), default_vol=30)
    adapter._get_active_target = AsyncMock(return_value="media_player.room")
    adapter._send_mlgw_steps = AsyncMock(return_value=False)
    adapter._send_ha_steps = AsyncMock(return_value=True)

    await adapter._apply_volume(39)

    adapter._send_mlgw_steps.assert_awaited_once_with("media_player.room", 6, 9.0)
    adapter._send_ha_steps.assert_awaited_once_with("media_player.room", 3, 9.0)
