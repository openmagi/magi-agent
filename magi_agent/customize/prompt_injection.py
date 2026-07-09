# DEPRECATED: this module was renamed to authored_prompt_append in U8
# (security-policies track). This one-line re-export shim will be removed
# after one release. Import from magi_agent.customize.authored_prompt_append
# instead.
from magi_agent.customize.authored_prompt_append import *  # noqa: F401, F403
from magi_agent.customize.authored_prompt_append import (  # noqa: F401
    VALUE_MAX,
    apply_prompt_injection_to_prompt_sections,
    apply_prompt_injection_to_tool_args,
    validate_prompt_injection_payload,
)
