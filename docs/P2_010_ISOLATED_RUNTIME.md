# P2-010 — Docker Isolated Runtime

P2-010 moves untrusted verification code out of the host process and into disposable Linux containers.

## Security boundary

Runtime containers are created with a fixed policy:

- `--network=none`;
- `--read-only` root filesystem;
- source worktree mounted read-only at `/input`;
- execution copy lives only in tmpfs `/workspace`;
- `--cap-drop=ALL`;
- `--security-opt no-new-privileges=true`;
- `--ipc=none`;
- explicit CPU, RAM and PID limits;
- no host secrets or inherited environment variables;
- no automatic image pulls during execution (`--pull=never`);
- fixed profile commands only; the API does not expose an arbitrary shell command field.

A tracked `.env`, private key, credential file or other P2-007 BLOCKED path prevents runtime execution entirely, even when the patch does not modify that file.

## Profiles

- `diff_check`: safe host Git metadata check;
- `python_compile`: Docker;
- `pytest`: Docker using the prepared Python runtime image;
- `frontend_build`: Docker using the prepared Node runtime image.

The runtime images are built explicitly with `prepare_p2_010_runtime.bat`. Network access may be used while building the trusted images, but runtime containers always use `network=none`.

## Artifacts

Known profile artifacts are copied from the stopped container with `docker cp`; the container never receives a writable host artifact mount. Export rejects symlinks and enforces file-count/size limits. Artifact ZIPs are downloadable from the runtime-run API.

## Boundary statement

Docker substantially improves process/resource/network/filesystem isolation over host execution, but it is not a mathematical security boundary. On Windows, stronger deployments may use Docker Desktop Enhanced Container Isolation or a separate VM/worker host.
