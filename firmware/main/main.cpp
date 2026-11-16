/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include <smooth_ui_toolkit.hpp>
#include <uitk/short_namespace.hpp>
#include <mooncake_log.h>
#include <mooncake.h>
#include <apps/app_setup/app_setup.h>
#include <hal/hal.h>
#include <hal/board/agent_boot_policy.h>

using namespace mooncake;
using namespace smooth_ui_toolkit;

extern "C" void app_main(void)
{
    // Setup logger
    mclog::set_level(mclog::level_info);
    mclog::set_time_format(mclog::time_format_unix_milliseconds);

    // HAL init
    GetHAL().init();

    // Setup ui hal
    ui_hal::on_delay([](uint32_t ms) { GetHAL().delay(ms); });
    ui_hal::on_get_tick([]() { return GetHAL().millis(); });
    {
        LvglLockGuard lock;
        GetHAL().bootLogo.reset();
    }

    const auto boot = dania::select_agent_boot(GetHAL().consumeAppSetupRequest(),
        GetHAL().getXiaozhiConfig().startAiAgentOnBoot, GetHAL().getWarmRebootTarget());
    GetHAL().clearWarmRebootRequest();
    if (boot == dania::AgentBoot::Maintenance) {
        // Mooncake is used only for the BLE provisioning extension. No app
        // launcher, gallery, dance or radio-control app is installed.
        AppSetup maintenance;
        maintenance.onOpen();
        while (!maintenance.isClosed()) {
            GetHAL().feedTheDog();
            GetHAL().updateHeapStatusLog();
            GetMooncake().update();
            maintenance.onRunning();
        }
        maintenance.onClose();
        DestroyMooncake();
    }

    // Start xiaozhi, never returns
    GetHAL().startXiaozhi();
}
