# Local-Use Security Boundary

Label-Tools is a single-computer annotation application, not a hardened multi-user server.
It only binds to `127.0.0.1`; there is deliberately no public-host option. Do not forward its port,
put it behind a public reverse proxy, or use it for untrusted multi-user workloads.

The service validates Host and Origin, requires a process token for JSON writes, limits request bodies,
rejects manifest paths outside the dataset, and sends frame/CSP headers. These checks do not provide
protection from other software running under your local user account. Use trusted local filesystems;
network filesystem lock behavior is not validated.

No telemetry, external frontend requests, accounts, or automatic uploads are included. Installing Python
dependencies or cloning the repository uses the network; annotating an existing workspace does not.

Never commit actual datasets, annotations, secrets, tokens, absolute private paths, or model weights.
The `.gitignore` is a convenience, not a substitute for reviewing staged files.

If you find a vulnerability, use GitHub's private reporting feature when available. Do not include
real private images or credentials in public issues; use a minimal synthetic reproducer.
