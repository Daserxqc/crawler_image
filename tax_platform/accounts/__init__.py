"""Login, watchlists, and email alerts (PR10).

Public crawler / normalize / search must not import this package.
The web app mounts routes via ``mount_accounts`` only.
"""

from tax_platform.accounts.router import mount_accounts

__all__ = ["mount_accounts"]
