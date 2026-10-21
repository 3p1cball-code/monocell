"""opencode launcher: starts the TUI inside a monocell terminal.

It runs opencode locally by default. Set a host in Settings > Tools to run a remote
instance over SSH instead; if the connection fails, the local command is tried.
"""
import os
import shlex

from ..app import App


class Opencode(App):
    name = 'opencode'
    title = 'opencode'
    icon = '[O]'
    size = (20, 6)

    @classmethod
    def launch(cls, wm):
        cfg = wm.cfg
        host = (cfg.get('opencode_host') or '').strip()
        cmd = (cfg.get('opencode_cmd') or 'opencode').strip()
        wd = (cfg.get('opencode_workdir') or '').strip()
        local_argv = shlex.split(cmd)
        local_s = ' '.join(shlex.quote(a) for a in local_argv)
        if host:
            remote = cmd if not wd else 'cd %s && %s' % (shlex.quote(wd), cmd)
            ssh_argv = ['ssh', '-t', '-o', 'ConnectTimeout=6', host, remote]
            ssh_s = ' '.join(shlex.quote(a) for a in ssh_argv)
            script = ('%s || { echo; echo "SSH to %s failed - starting %s locally..."; '
                      'sleep 2; exec %s; }' % (ssh_s, host, cmd, local_s))
            term = wm.open('terminal', cmd=['/bin/sh', '-c', script], cwd=os.path.expanduser('~'),
                           close_on_exit=False)
            title = 'opencode @ ' + host.split('@')[-1]
        else:
            term = wm.open('terminal', cmd=local_argv,
                           cwd=os.path.expanduser(wd) if wd else os.path.expanduser('~'))
            title = 'opencode'
        if term is not None:
            term.fixed_title = title
            term.set_title(title)
        return None


APP = Opencode
