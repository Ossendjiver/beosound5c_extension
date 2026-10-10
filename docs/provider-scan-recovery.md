# Automatic provider scan recovery

Enable beo-provider-resume.timer after installing its service and timer templates.
Every half hour (and after a missed schedule during reboot), it checks pending
profiles. Completed sweeps and idle players are required; a shared nonblocking
lock prevents duplicate scans. Completed profiles are retained. Failed tracks
respect their one-day retry delay, avoiding repeated requests every half hour.
The job uses the SSD runtime/cache, 25% CPU, 512 MiB memory and idle I/O.
An active scan is left running; recovery waits for the next timer activation.
