SCGI: A Simple Common Gateway Interface alternative
===================================================

Protocol
--------

    See http://python.ca/nas/scgi/protocol.txt for the specification of
    the SCGI protocol.


Software
--------

    scgi
    ----

        A Python package implementing the server side of the SCGI
        protocol.


    apache1
    -------

        An Apache 1.3 module that implements the client side of the
        protocol.  See the README file in the apache1 directory for more
        details. 


    apache2
    -------

        An Apache 2.0 module that implements the client side of the
        protocol.  See the README file in the apache2 directory for more
        details.  NOTE: this module has not received a lot of testing
        yet and may still be unreliable.


    cgi2scgi
    --------
    
        A CGI script that forwards requests to a SCGI server.  This is
        useful in situations where you cannot or do not want to use the
        mod_scgi module.  Because the CGI script is small performance is
        quite good.

        To use, edit the source and specify the correct address and port
        of the SCGI server.  Next, compile using a C compiler, e.g.:

            $ cc -o myapp.cgi cgi2scgi.c

       Finally, put the script in the proper directory (depends on web
       server software used).


License
-------

    The SCGI package is copyrighted and made available under open source
    licensing terms.  See LICENSE.txt for the details.  The CHANGES.txt
    file summarizes the changes made since version 1.10 and lists the
    authors of those changes.


/* vim: set ai tw=74 et sw=4 sts=4: */
