---
id: user-harness:file-delivery-after-create
trigger: beforeCommit
condition:
  anyToolUsed:
    - DocumentWrite
    - SpreadsheetWrite
    - FileWrite
    - FileEdit
action:
  type: require_tool
  toolName: FileDeliver
enforcement: block_on_fail
timeoutMs: 2000
---

When a file, document, spreadsheet, report, or artifact is created or modified for the user, deliver it to the chat before claiming completion.
