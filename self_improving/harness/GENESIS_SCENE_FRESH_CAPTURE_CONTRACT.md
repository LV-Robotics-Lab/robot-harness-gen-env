# Restored final-state camera execution

`execute_genesis_fresh_capture(store, request_ref, roots)` runs an independent
fixed Genesis camera process and returns `GenesisFreshCaptureResultV1`. A caller
must first establish the request's registered replay lineage; the launcher itself
does not issue a Skill qualification or an observation receipt.

The request names the source replay receipt, scene, half-dt rows and exact final
object state at step 2000 / simulation time 4 seconds. The new process loads the
same representation, finite fixed table and fixed ground. It restores the object's
pose through Genesis's entity user-frame setters (`relative=True`), matching the
source trace's default getters, then zeros all six DOF velocities. It reads the
pose and velocities back and requires positional agreement within 1e-6 metres,
equivalent unit quaternions and zero linear/angular velocity. No physics step runs.

This is **restored final-state observation**, not continuation of the old simulation
process and not an assertion that source velocities have been reproduced. Raw load
evidence retains both the requested source state and actual restored state, the
coordinate-frame and velocity policies, entity collision counts and camera setup.

The camera renders a new 640×480 RGB image using checked OSMesa framebuffers and
actual Mesa llvmpipe. UTC `captured_at` is collected immediately after `camera.render`
returns; it is not derived from file modification times or the old frame. PNG bytes,
decoded pixel SHA-256, camera parameters and renderer identity remain linked to the
raw process observation and exact request. The owning application checks freshness
after verification and preserves the original timestamp on historical reads.

The child inherits the already verified scene Landlock/seccomp policy: runtime
roots are read-only, output writes remain inside its own run, and network calls are
denied. It probes the denials and retains actual Python/module/native/environment
identity. The launcher verifies the independent observer manifest before and after
execution, maps every observed member to it and retains stdout, stderr, partial
files and a canonical POSIX-string-sorted index on failure.

The qualified replay V2 files and manifest `30587ef440c0467cc211fe5ee00b6256dca605d0ee2119fe629b62953ea32e93`
are unchanged. The observer adds three files to a separate backend root, while
reusing the existing five deployment roots and CAS. Finite-table loading and the
software renderer derive from the Harness scene adapter; its Gujie provenance is
fixed at `eb0b710581fd7794bc01b447b0f77cb871c8a711`, and Genesis remains owned by
Genesis World at the previously fixed simulator revision.

The constrained smoke run produced a real new PNG at
`/home/jingxiang/bingsheng/golden-fresh-capture-smoke-20260911.zzn9y2ot/run/capture/fresh.png`.
Its raw load, camera event, process observation, streams and result are retained
beside it. Smoke evidence does not itself grant production observation authority.
