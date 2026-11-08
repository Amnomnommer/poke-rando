# Release roadmap

| Milestone | Exit criterion |
|---|---|
| White 2 Alpha | Build, boot, randomize, save/reload, party, PC and Pokédex pass for the current species cap |
| White 2 1.0 | Every 1.0 domain in `product-scope.md` is implemented, structurally verified and runtime accepted |
| 1.1 | White 2 run systems, lead follower and optional gimmick contract pass |
| 1.1.5 | One representative mainline ROM game from each generation reaches 1.1 |
| 1.2 | Overworld encounter option passes on the representative games |
| 1.2.5 | Every supported game reaches 1.2 parity |
| 1.3 | Streamlined story passes per representative game/generation |
| 1.3.5 | Every supported game reaches 1.3 parity |
| 1.4 | Provenance-checked character sprite customization passes |

## Implementation order

1. Keep White 2 as the only build target until its current diagnostic and first
   randomized alpha are stable.
2. Add Quick-Start Supplies once the early item-award script is proven.
3. Add Fast Start and Egg Mode using explicit adapter sites and save-state
   verification.
4. Implement each Generation 9 mechanics domain separately; never label the
   adapter 1.0 until every required domain is complete.
5. Reuse the Generation 5 implementation for Black 2, then Black/White.
6. Advance one game at a time through the engine-family coverage plan.

Synthetic tests prove planners and codecs. They do not replace a real build,
emulator test, save/reload test, or playthrough acceptance.
