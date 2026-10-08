#!/bin/sh
set -eu
# Fail closed: only Squid may open external sockets; Chromium cannot bypass the proxy.
for tool in iptables ip6tables; do
  "$tool" -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  "$tool" -A OUTPUT -o lo -j ACCEPT
  "$tool" -A OUTPUT -m owner --uid-owner proxy -j ACCEPT
  "$tool" -A OUTPUT -j REJECT
done
squid -N &
exec setpriv --reuid=node --regid=node --init-groups --bounding-set=-all python /opt/service/server.py
