#!/bin/sh
security find-generic-password -s 'Claude Code-credentials' -w | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])"
