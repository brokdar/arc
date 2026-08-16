"""Outbound clients for the services that hold the athlete's files.

**arc holds the credential and reads the folder over the provider's own API.**
The obvious alternative was a courier: `rclone`, or Syncthing, mirroring the
Dropbox folder into `data/inbox/` so the existing sweep picks the files up and
arc needs no connector at all. It was rejected for three reasons, in increasing
order of importance:

1. it is a second stack to deploy and keep running on a box whose whole appeal
   is that it is one `docker compose up`;
2. it needs a mount arranged between two containers, which is exactly the class
   of deployment detail that works on the developer's machine and not on the
   operator's;
3. and — the one that decides it — the part most likely to break would live
   *outside* the application that depends on it. When the credential dies or
   the sync stops, arc would see an empty folder and have nothing to say. A
   feature whose entire purpose is that a missing ride cannot go unnoticed
   cannot be built on a component it cannot interrogate.

So this layer exists, and it sits between `app.services` and
`app.persistence` in the import-linter contract: a connector may read and write
the connection row whose credential it refreshes, and may never reach up into
a service.
"""
