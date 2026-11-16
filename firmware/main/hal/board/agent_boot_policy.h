#pragma once

namespace dania {
enum class AgentBoot { Agent, Maintenance };
// Legacy launcher indices and the old boot preference never select removed apps.
constexpr AgentBoot select_agent_boot(bool maintenance, bool, int) {
    return maintenance ? AgentBoot::Maintenance : AgentBoot::Agent;
}
}
