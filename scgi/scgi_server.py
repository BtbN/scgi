#!/usr/bin/env python
"""
A pre-forking SCGI server that uses file descriptor passing to off-load
requests to child worker processes.
"""

import sys
import socket
import os
import select
import errno
import fcntl
import signal
from scgi import passfd

# netstring utility functions
def ns_read_size(input):
    size = ""
    while 1:
        c = input.read(1)
        if c == ':':
            break
        elif not c:
            raise IOError, 'short netstring read'
        size = size + c
    return long(size)

def ns_reads(input):
    size = ns_read_size(input)
    data = ""
    while size > 0:
        s = input.read(size)
        if not s:
            raise IOError, 'short netstring read'
        data = data + s
        size -= len(s)
    if input.read(1) != ',':
        raise IOError, 'missing netstring terminator'
    return data

def read_env(input):
    headers = ns_reads(input)
    items = headers.split("\0")
    items = items[:-1]
    assert len(items) % 2 == 0, "malformed headers"
    env = {}
    for i in range(0, len(items), 2):
        env[items[i]] = items[i+1]
    return env

class SCGIHandler:

    # Subclasses should override the handle_connection method.

    def __init__(self, parent_fd):
        self.parent_fd = parent_fd

    def serve(self):
        while 1:
            try:
                os.write(self.parent_fd, "1") # indicates that child is ready
                fd = passfd.recvfd(self.parent_fd)
            except (IOError, OSError):
                # parent probably exited  (EPIPE comes thru as OSError)
                raise SystemExit
            conn = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
            # Make sure the socket is blocking.  Apparently, on FreeBSD the
            # socket is non-blocking.  I think that's an OS bug but I don't
            # have the resources to track it down.
            conn.setblocking(1)
            os.close(fd)
            self.handle_connection(conn)


    def read_env(self, input):
        return read_env(input)

    def handle_connection(self, conn):
        input = conn.makefile("r")
        output = conn.makefile("w")

        env = self.read_env(input)
        output.write("Content-Type: text/plain\r\n")
        output.write("\r\n")
        for k, v in env.items():
            output.write("%s: %r\n" % (k, v))

        output.close()
        input.close()
        conn.close()


class SCGIServer:

    DEFAULT_PORT = 4000

    def __init__(self, handler_class=SCGIHandler, host="", port=DEFAULT_PORT,
                 max_children=5):
        self.handler_class = handler_class
        self.host = host
        self.port = port
        self.max_children = max_children
        self.children = {} # { pid : fd }
        self.spawn_child()
        self.restart = 0
        
    #
    # Deal with a hangup signal.  All we can really do here is
    # note that it happened.
    #
    def hup_signal(self, signum, frame):
        self.restart = 1

    def spawn_child(self, conn=None):
        parent_fd, child_fd = passfd.socketpair(socket.AF_UNIX,
                                                socket.SOCK_STREAM)
        # make child fd non-blocking
        flags = fcntl.fcntl(child_fd, fcntl.F_GETFL, 0)
        fcntl.fcntl(child_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        pid = os.fork()
        if pid == 0:
            if conn:
                conn.close() # in the midst of handling a request, close
                             # the connection in the child 
            os.close(child_fd)
            self.handler_class(parent_fd).serve()
            sys.exit(0)
        else:
            os.close(parent_fd)
            self.children[pid] = child_fd

    def reap_children(self):
        while self.children:
            (pid, status) = os.waitpid(-1, os.WNOHANG)
            if pid <= 0:
                break
            os.close(self.children[pid])
            del self.children[pid]

    def do_restart(self):
        #
        # First close connections to the children, which will cause them
        # to exit after finishing what they are doing.
        #
        for fd in self.children.values():
            os.close(fd)
        #
        # Then do a blocking wait on each until we have cleared the
        # slate.
        #
        for pid in self.children.keys():
            (pid, status) = os.waitpid(pid, 0)
        self.children = {}
        #
        # Fire off a new child, we'll be wanting it soon.
        #
        self.spawn_child()
        self.restart = 0
        

    def delegate_request(self, conn):
        """Pass a request fd to a child process to handle.  This method
        blocks if all the children are busy and we have reached the
        max_children limit."""

        # There lots of subtleties here.  First, we can't use the write
        # status of the pipes to the child since select will return true
        # if the buffer is not filled.  Instead, each child writes one
        # byte of data when it is ready for a request.  The normal case
        # is that a child is ready for a request.  We want that case to
        # be fast.  Also, we want to pass requests to the same child if
        # possible.  Finally, we need to gracefully handle children
        # dying at any time.

        # If no children are ready and we haven't reached max_children
        # then we want another child to be started without delay.
        timeout = 0

        while 1:
            try:
                r, w, e = select.select(self.children.values(), [], [], timeout)
            except select.error, e:
                if e[0] == errno.EINTR:  # got a signal, try again
                    continue
                raise
            if r:
                # One or more children look like they are ready.  Sort
                # the file descriptions so that we keep preferring the
                # same child.
                r.sort()
                child_fd = r[0]

                # Try to read the single byte written by the child.
                # This can fail if the child died or the pipe really
                # wasn't ready (select returns a hint only).  The fd has
                # been made non-blocking by spawn_child.  If this fails
                # we fall through to the "reap_children" logic and will
                # retry the select call.
                try:
                    ready_byte = os.read(child_fd, 1)
                    if not ready_byte:
                        raise IOError # child died?
                    assert ready_byte == "1", repr(ready_byte)
                except socket.error, exc:
                    if exc[0]  == errno.EWOULDBLOCK:
                        pass # select was wrong
                    else:
                        raise
                except (OSError, IOError):
                    pass # child died?
                else:
                    # The byte was read okay, now we need to pass the fd
                    # of the request to the child.  This can also fail
                    # if the child died.  Again, if this fails we fall
                    # through to the "reap_children" logic and will
                    # retry the select call.
                    try:
                        passfd.sendfd(child_fd, conn.fileno())
                    except IOError, exc:
                        if exc.errno == errno.EPIPE:
                            pass # broken pipe, child died?
                        else:
                            raise
                    else:
                        # fd was apparently passed okay to the child.
                        # The child could die before completing the
                        # request but that's not our problem anymore.
                        return

            # didn't find any child, check if any died
            self.reap_children()
            
            # start more children if we haven't met max_children limit
            if len(self.children) < self.max_children:
                self.spawn_child(conn)
            
            # Start blocking inside select.  We might have reached
            # max_children limit and they are all busy.
            timeout = 2


    def serve(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(40)
        signal.signal(signal.SIGHUP, self.hup_signal)
        while 1:
            try:
                conn, addr = s.accept()
                self.delegate_request(conn)
                conn.close()
            except socket.error, e:
                if e[0] != errno.EINTR:
                    raise  # something weird
            if self.restart:
                self.do_restart()


def main():
    if len(sys.argv) == 2:
        port = int(sys.argv[1])
    else:
        port = SCGIServer.DEFAULT_PORT
    SCGIServer(port=port).serve()

if __name__ == "__main__":
    main()
