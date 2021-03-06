#!/usr/bin/env python

import os
import glob
import sys
import platform
import subprocess
import difflib
import filecmp
import shutil

from optparse import OptionParser


#
# Get standard testsuite test arguments: srcdir exepath
#

srcdir = "."
tmpdir = "."
path = "../.."

# Options for the command line
parser = OptionParser()
parser.add_option("-p", "--path", help="add to executable path",
                  action="store", type="string", dest="path", default="")
parser.add_option("--devenv-config", help="use a MS Visual Studio configuration",
                  action="store", type="string", dest="devenv_config", default="")
parser.add_option("--solution-path", help="MS Visual Studio solution path",
                  action="store", type="string", dest="solution_path", default="")
(options, args) = parser.parse_args()

if args and len(args) > 0 :
    srcdir = args[0]
    srcdir = os.path.abspath (srcdir) + "/"
    os.chdir (srcdir)
if args and len(args) > 1 :
    path = args[1]
path = os.path.normpath (path)

tmpdir = "."
tmpdir = os.path.abspath (tmpdir)

refdir = "ref/"
refdirlist = [ refdir ]
parent = "../../../../../"
test_source_dir = "../../../../testsuite/" + os.path.basename(os.path.abspath(srcdir))

command = ""
outputs = [ "out.txt" ]    # default
failureok = 0
failthresh = 0.004
hardfail = 0.012
failpercent = 0.02
anymatch = False

image_extensions = [ ".tif", ".tx", ".exr", ".jpg", ".png", ".rla",
                     ".dpx", ".iff", ".psd" ]

compile_osl_files = True
splitsymbol = ';'

#print ("srcdir = " + srcdir)
#print ("tmpdir = " + tmpdir)
#print ("path = " + path)
#print ("refdir = " + refdir)
print ("test source dir = " + test_source_dir)

if platform.system() == 'Windows' :
    if not os.path.exists("./ref") :
        shutil.copytree (os.path.join (test_source_dir, "ref"), "./ref")
    if os.path.exists (os.path.join (test_source_dir, "src")) and not os.path.exists("./src") :
        shutil.copytree (os.path.join (test_source_dir, "src"), "./src")
    # if not os.path.exists("../data") :
    #     shutil.copytree ("../../../testsuite/data", "..")
    # if not os.path.exists("../common") :
    #     shutil.copytree ("../../../testsuite/common", "..")
else :
    if not os.path.exists("./ref") :
        os.symlink (os.path.join (test_source_dir, "ref"), "./ref")
    if os.path.exists (os.path.join (test_source_dir, "src")) and not os.path.exists("./src") :
        os.symlink (os.path.join (test_source_dir, "src"), "./src")
    if not os.path.exists("./data") :
        os.symlink (test_source_dir, "./data")
    if not os.path.exists("../common") and os.path.exists("../../../testsuite/common") :
        os.symlink ("../../../testsuite/common", "../common")


###########################################################################

# Handy functions...

# Compare two text files. Returns 0 if they are equal otherwise returns
# a non-zero value and writes the differences to "diff_file".
# Based on the command-line interface to difflib example from the Python
# documentation
def text_diff (fromfile, tofile, diff_file=None):
    import time
    try:
        fromdate = time.ctime (os.stat (fromfile).st_mtime)
        todate = time.ctime (os.stat (tofile).st_mtime)
        fromlines = open (fromfile, 'rU').readlines()
        tolines   = open (tofile, 'rU').readlines()
    except:
        print ("Unexpected error:", sys.exc_info()[0])
        return -1
        
    diff = difflib.unified_diff(fromlines, tolines, fromfile, tofile,
                                fromdate, todate)
    # Diff is a generator, but since we need a way to tell if it is
    # empty we just store all the text in advance
    diff_lines = [l for l in diff]
    if not diff_lines:
        return 0
    if diff_file:
        try:
            open (diff_file, 'w').writelines (diff_lines)

            print ("Diff " + fromfile + " vs " + tofile + " was:\n-------")
#            print (diff)
            print ("".join(diff_lines))
        except:
            print ("Unexpected error:", sys.exc_info()[0])
    return 1



def my_app (app):
    # when we use Visual Studio, built applications are stored
    # in the app/$(OutDir)/ directory, e.g., Release or Debug.
    # In that case the special token "$<CONFIGURATION>" which is replaced by
    # the actual configuration if one is specified. "$<CONFIGURATION>" works
    # because on Windows it is a forbidden filename due to the "<>" chars.
    if (platform.system () == 'Windows'):
        return app + "/$<CONFIGURATION>/" + app + " "
    return path + "/src/" + app + "/" + app + " "


def oiio_relpath (path, start=os.curdir):
    "Wrapper around os.path.relpath which always uses '/' as the separator."
    p = os.path.relpath (path, start)
    return p if sys.platform != "win32" else p.replace ('\\', '/')


def oiio_app (app):
    if os.environ.__contains__('OPENIMAGEIOHOME') :
        return os.path.join (os.environ['OPENIMAGEIOHOME'], "bin", app) + " "
    else :
        return app + " "


# Construct a command that runs oiiotool, appending console output
# to the file "out.txt".
def oiiotool (args) :
    return (oiio_app("oiiotool") + args + " >> out.txt 2>&1 ;\n")


# Construct a command that will compare two images, appending output to
# the file "out.txt".  We allow a small number of pixels to have up to
# 1 LSB (8 bit) error, it's very hard to make different platforms and
# compilers always match to every last floating point bit.
def oiiodiff (fileA, fileB, extraargs="", silent=True, concat=True) :
    command = (oiio_app("idiff") + "-a"
               + " -fail " + str(failthresh)
               + " -failpercent " + str(failpercent)
               + " -hardfail " + str(hardfail)
               + " -warn " + str(2*failthresh)
               + " -warnpercent " + str(failpercent)
               + " " + extraargs + " " + oiio_relpath(fileA,tmpdir)
               + " " + oiio_relpath(fileB,tmpdir))
    if not silent :
        command += " >> out.txt 2>&1 "
    if concat:
        command += " ;\n"
    return command


# Check one output file against reference images in a list of reference
# directories. For each directory, it will first check for a match under
# the identical name, and if that fails, it will look for alternatives of
# the form "basename-*.ext" (or ANY match in the ref directory, if anymatch
# is True).
def checkref (name, refdirlist) :
    # Break the output into prefix+extension
    (prefix, extension) = os.path.splitext(name)
    ok = 0
    for ref in refdirlist :
        # We will first compare name to ref/name, and if that fails, we will
        # compare it to everything else that matches ref/prefix-*.extension.
        # That allows us to have multiple matching variants for different
        # platforms, etc.
        defaulttest = os.path.join(ref,name)
        if anymatch :
            pattern = "*.*"
        else :
            pattern = prefix+"-*"+extension+"*"
        for testfile in ([defaulttest] + glob.glob (os.path.join (ref, pattern))) :
            if not os.path.exists(testfile) :
                continue
            # print ("comparing " + name + " to " + testfile)
            if extension in image_extensions :
                # images -- use idiff
                cmpcommand = diff_command (name, testfile, concat=False, silent=True)
                cmpresult = os.system (cmpcommand)
            elif extension == ".txt" :
                cmpresult = text_diff (name, testfile, name + ".diff")
            else :
                # anything else
                cmpresult = 0
                if os.path.exists(testfile) and filecmp.cmp (name, testfile) :
                    cmpresult = 0
                else :
                    cmpresult = 1
            if cmpresult == 0 :
                return (True, testfile)   # we're done
    return (False, defaulttest)



# Run 'command'.  For each file in 'outputs', compare it to the copy
# in 'ref/'.  If all outputs match their reference copies, return 0
# to pass.  If any outputs do not match their references return 1 to
# fail.
def runtest (command, outputs, failureok=0, failthresh=0, failpercent=0) :
#    print ("working dir = " + tmpdir)
    os.chdir (srcdir)
    open ("out.txt", "w").close()    # truncate out.txt

    if options.path != "" :
        sys.path = [options.path] + sys.path
    print "command = " + command

    if (platform.system () == 'Windows'):
        # Replace the /$<CONFIGURATION>/ component added in oiio_app
        oiio_app_replace_str = "/"
        if options.devenv_config != "":
            oiio_app_replace_str = '/' + options.devenv_config + '/'
        command = command.replace ("/$<CONFIGURATION>/", oiio_app_replace_str)

    test_environ = None
    if (platform.system () == 'Windows') and (options.solution_path != "") and \
       (os.path.isdir (options.solution_path)):
        test_environ = os.environ
        libOIIO_path = options.solution_path + "\\libOpenImageIO\\"
        if options.devenv_config != "":
            libOIIO_path = libOIIO_path + '\\' + options.devenv_config
        test_environ["PATH"] = libOIIO_path + ';' + test_environ["PATH"]

    for sub_command in command.split(splitsymbol):
        cmdret = subprocess.call (sub_command, shell=True, env=test_environ)
        if cmdret != 0 and failureok == 0 :
            print "#### Error: this command failed: ", sub_command
            print "FAIL"
            return (1)

    err = 0
    for out in outputs :
        (prefix, extension) = os.path.splitext(out)
        (ok, testfile) = checkref (out, refdirlist)

        if ok :
            if extension in image_extensions :
                # If we got a match for an image, save the idiff results
                os.system (diff_command (out, testfile, silent=False))
            print ("PASS: " + out + " matches " + testfile)
        else :
            err = 1
            print ("NO MATCH for " + out)
            print ("FAIL " + out)
            if extension == ".txt" :
                # If we failed to get a match for a text file, print the
                # file and the diff, for easy debugging.
                print ("-----" + out + "----->")
                print (open(out,'r').read() + "<----------")
                print ("-----" + testfile + "----->")
                print (open(testfile,'r').read() + "<----------")
                os.system ("ls -al " +out+" "+testfile)
                print ("Diff was:\n-------")
                print (open (out+".diff", 'rU').read())
            if extension in image_extensions :
                # If we failed to get a match for an image, send the idiff
                # results to the console
                os.system (diff_command (out, testfile, silent=False))
            if os.path.isfile("debug.log") and os.path.getsize("debug.log") :
                print ("---   DEBUG LOG   ---\n")
                with open("debug.log", "r") as flog :
                    print flog.read()
                print ("--- END DEBUG LOG ---\n")
    return (err)


##########################################################################



#
# Read the individual run.py file for this test, which will define 
# command and outputs.
#
with open(os.path.join(test_source_dir,"run.py")) as f:
    code = compile(f.read(), "run.py", 'exec')
    exec (code)
# if os.path.exists("run.py") :
#     execfile ("run.py")

# Allow a little more slop for slight pixel differences when in DEBUG
# mode or when running on remote Travis-CI or Appveyor machines.
if (("TRAVIS" in os.environ and os.environ["TRAVIS"]) or
    ("APPVEYOR" in os.environ and os.environ["APPVEYOR"]) or
    ("DEBUG" in os.environ and os.environ["DEBUG"])) :
    failthresh *= 2.0
    hardfail *= 2.0
    failpercent *= 2.0

# Force out.txt to be in the outputs
##if "out.txt" not in outputs :
##    outputs.append ("out.txt")

# If either out.exr or out.tif is in the reference directory but somehow
# is not in the outputs list, put it there anyway!
if (os.path.exists("ref/out.exr") and ("out.exr" not in outputs)) :
    outputs.append ("out.exr")
if (os.path.exists("ref/out.tif") and ("out.tif" not in outputs)) :
    outputs.append ("out.tif")

# Run the test and check the outputs
ret = runtest (command, outputs, failureok=failureok,
               failthresh=failthresh, failpercent=failpercent)
sys.exit (ret)
