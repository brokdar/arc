// Strength numbers the phone computes for itself.
//
// Display arithmetic only. The phone has to show a loadable weight, a plate
// breakdown, a starting weight and a mid-session step-down in a basement gym
// with no signal, so it cannot ask the server — but arc's training rules
// (progression, caps, what counts as a miss) stay in Python, where the coach
// over MCP and the API read them from one place. What lives here is the small
// arithmetic that turns a number the server already decided into something
// that can go on the bar, ported line for line from `app/domain/loading.py`.
// One rule is the phone's own: the step-down after a missed set
// (`components/strength/step-down.ts`), which answers a set the server has
// not seen yet and so cannot come prepared in the packet; its docstring says
// why, and it only uses `stepDown` from here.
//
// That displaces porting the rules themselves to TypeScript: two
// implementations of a progression rule drift, and the drift would show up as
// the phone and the coach suggesting different weights for the same set. The
// arithmetic cannot drift silently either — `loading.vectors.test.ts` runs
// every case in `backend/tests/vectors/loading.json`, which the Python domain
// emits (`just loading-vectors`), and demands the identical answer.

export * from "./loading";
