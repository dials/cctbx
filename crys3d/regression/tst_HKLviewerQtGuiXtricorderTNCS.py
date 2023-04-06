from __future__ import absolute_import, division, print_function
from crys3d.regression import tests_PhenixHKLviewer as tsthkl

# With HKLviewer Qt GUI run xtricorder on 1upp_lowres.mtz to create and load 1upp_lowres_xtricorder.mtz.
# Test for the indices of visible P1 and Friedel expanded TEPS reflections when the sphere of
# reflections is sliced perpendicular to the TNCS vector at layer 33 and reflections have been
# divided into 5 bins according to TNCS modulation values but with explicit bin threshold values and
# only reflections of the 5th bin are displayed
def run():
  count = 0
  while True:
    print("running %d" %count)
    # websockets employed by HKLviewer is slightly unstable on virtual machines used in CI on Azure.
    # This might yield a bogus failure of the test. If so, repeat the test at most maxruns times
    # or until it passes whichever comes first.
    if not tsthkl.runagain(tsthkl.exerciseQtGUI,
                                    tsthkl.philstr1,
                                    tsthkl.reflections2match1,
                                    "QtGuiXtricorderTNCS"):
      break
    count +=1
    assert(count < tsthkl.maxruns)


if __name__ == '__main__':
  run()