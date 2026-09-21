# Browser Use agent study

Two tasks are pinned before candidate execution in `../cases.json`. The
candidate removes only the bounded `<browser_use_docs>` reference block from
`AGENTS.md`; development rules outside that block are unchanged. Generated
candidate patches and raw results belong under `candidates/` and `results/`.

Both hidden graders now fail on their pre-fix commit and pass on the exact
upstream fixed commit. Their shared candidate reduces `AGENTS.md` from 9,616 to
579 estimated tokens by removing only the bounded docs block. No paired agent
trial has run, so no quality or cost claim is verified.
