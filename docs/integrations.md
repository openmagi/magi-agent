# Integrations

Integrations connect Magi Agent to external systems. They should be explicit,
scoped, and reversible.

## Composio

Composio can be used as an optional connector layer. Open-source users should
create their own Composio account and provide their own credentials when they
want to connect external apps.

Do not enable an external integration just because a credential exists. Require
an explicit integration setting, toolkit scope, and user approval for
credentialed actions.

## Local and custom tools

Custom tools should declare:

- required credentials;
- allowed operations;
- mutation behavior;
- approval requirements;
- receipt fields;
- public projection rules.

