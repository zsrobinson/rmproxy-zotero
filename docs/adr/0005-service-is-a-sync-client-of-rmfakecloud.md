# The service talks to rmfakecloud as a sync client, like the tablet does

The service speaks rmfakecloud's device sync protocol (as rmapi does) and appears to rmfakecloud as just another device, rather than reading and writing rmfakecloud's storage directory or forking rmfakecloud to add an API. The storage layout is undocumented and easy to corrupt; a fork would have to be maintained forever. The protocol is the one interface rmfakecloud must keep stable for the tablet itself.
