# -*- coding: utf-8 -*-
"""Ocean Agent MCP entry point for the one-click bundle.

The bundle declares ocean-agent as a dependency, so all the real code lives
in that package. This file only starts it, keeping the bundle to a manifest
plus a few lines means a version bump is a dependency change, not a repack.
"""
import sys

from ocean_agent.mcp_server import main

if __name__ == "__main__":
    sys.exit(main())
