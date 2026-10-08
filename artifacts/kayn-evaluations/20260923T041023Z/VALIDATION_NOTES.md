# Validation Notes

## Valid result boundary

- Single-turn run `b123419b-a12e-47d8-b0ac-5104d3341e71` is valid: all 23 cases completed with no infrastructure errors.
- Multi-turn run `28135727-2623-4984-bcb7-ea57bc55848d` confirms that all six conversation metrics now execute without evaluator errors.
- The multi-turn business score is not a valid Agent quality result because the connector-managed Target mapping did not forward `target_context` to the Agent.

## Kayn integration gap

The updated Kayn conversation mapper emits `target_context`, but the SDK Target default request mapping still forwards only `input`, `history`, `messages`, `conversation_id`, and `conversation_id_int`. Runtime preflight also rejects `target_context` as an unsupported Target variable. Consequently, multi-turn calls entered the Agent through the legacy v1 path instead of the page-context-aware runtime.

A temporary local mapping update was attempted to verify the diagnosis. Kayn preflight rejected it with `TARGET_INPUT_VARIABLE_UNSUPPORTED`, confirming that the missing contract spans both SDK Target registration and runtime preflight. The temporary mapping was restored after diagnosis.

## Transport configuration

The evaluation compose override now sets both the Kayn connector limit and Tomcat's actual WebSocket text buffer to 16 MiB. This removed the earlier close-code `1009` failures and allowed the valid single-turn run to complete without network or execution errors.
