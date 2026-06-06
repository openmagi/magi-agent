# magi_agent/recipes/first_party/legal/rule_inject.py
from __future__ import annotations

# Explicit statements of the legal rule each task tests. These rules are rare in
# pretraining, so stating them is the highest-reliability LegalBench lever for
# rule-conclusion / rule-application tasks. Extend per curated task.
RULE_STATEMENTS: dict[str, str] = {
    "abercrombie": (
        "Rule: Trademark distinctiveness falls on the Abercrombie spectrum: "
        "generic (never protectable), descriptive (protectable only with "
        "secondary meaning), suggestive, arbitrary, or fanciful (inherently "
        "distinctive). A mark is generic when it names the product category "
        "itself."
    ),
    "hearsay": (
        "Rule: Hearsay is an out-of-court statement offered to prove the truth "
        "of the matter asserted. A statement offered for a non-truth purpose "
        "(e.g., effect on the listener, notice, or a verbal act) is not hearsay."
    ),
    "contract_nli_explicit_identification": (
        "Rule: A contract party is explicitly identified when the agreement names "
        "it by its legal entity name, trade name, or a defined term that "
        "unambiguously designates it. Implicit identification or functional "
        "descriptions alone do not satisfy explicit identification."
    ),
    "contract_nli_notice_on_compelled_disclosure": (
        "Rule: A notice-on-compelled-disclosure clause requires a party who is "
        "legally compelled to disclose confidential information (e.g., by court "
        "order or regulatory demand) to first give the other party prompt written "
        "notice, to the extent legally permitted, so that party may seek a "
        "protective order or other relief."
    ),
}


def inject_rule(prompt_body: str, *, task_id: str) -> str:
    rule = RULE_STATEMENTS.get(task_id)
    if rule is None:
        return prompt_body
    return f"{rule}\n\n{prompt_body}"
