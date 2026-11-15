# Wishlist

Public wishlists on https://wishlist.alchemmist.xyz. How the pieces fit together is in [OVERVIEW.md](OVERVIEW.md).

A signed-in person has one list at `/{username}`. The username comes from Authentik. Anyone can open that page and reserve a gift with a name. Names are unique: the same spelling, ignoring case, is one person. Releasing a gift requires that same name.

Sign-in is Authentik OIDC. The account must be in the group `app-wishlist-user`. After login the corner link becomes the Authentik avatar.

Pictures go to the RustFS bucket `wishlist` when `S3_ENDPOINT`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` are set. Without them they stay on the SQLite volume under `/data/media`.

SQLite lives on a single-node volume. The pod is pinned to `socrat-0` for that reason.
