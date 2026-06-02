## MakoSync v0.3.6

**Official results now wait for scoring.** Manager mode reads `Event.Event_stat`
and pushes a per-heat `scored` flag (`Event_stat == 'S'`). makosmeets promotes a
Meet Manager result to the public TV / meet page **only once its event is
scored** — until then the Dolphin (unofficial) time keeps showing, so an operator
can fix a mis-entry before scoring without it going out. The MM watcher's dedup
hash now folds in `scored`, so scoring (or un-scoring to fix an error) re-pushes
even when no lane time changed.

**do3 import filename preserves the race number.** Relayed Dolphin `.do3` files
are now renamed by **suffixing** `_E##_H##` onto the original name
(`015-000-00F0005.do3` → `015-000-00F0005_E22_H02.do3`) instead of rebuilding it.
This keeps the Dolphin race number intact for Meet Manager's *Get Times by Race
Number* import, while the suffix still labels the heat for the manual file-pick
import.
