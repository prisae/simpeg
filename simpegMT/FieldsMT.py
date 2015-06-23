from SimPEG import Survey, Utils, Problem, np, sp, mkvc
from scipy.constants import mu_0
import sys
from numpy.lib import recfunctions as recFunc
from simpegEM.Utils.EMUtils import omega

##############
### Fields ###
##############
class FieldsMT(Problem.Fields):
    """Field Storage for a MT survey."""
    knownFields = {}
    dtype = complex


class FieldsMT_1D(FieldsMT):
    """
    Fields storage for the 1D MT solution.
    """
    knownFields = {'e_1dSolution':'F'}
    aliasFields = {
                    'e_1d' : ['e_1dSolution','F','_e'],
                    'e_1dPrimary' : ['e_1dSolution','F','_ePrimary'],
                    'e_1dSecondary' : ['e_1dSolution','F','_eSecondary'],
                    'b_1d' : ['e_1dSolution','E','_b'],
                    'b_1dPrimary' : ['e_1dSolution','E','_bPrimary'],
                    'b_1dSecondary' : ['e_1dSolution','E','_bSecondary']
                  }

    def __init__(self,mesh,survey,**kwargs):
        FieldsMT.__init__(self,mesh,survey,**kwargs)

    def _ePrimary(self, eSolution, srcList):
        ePrimary = np.zeros_like(eSolution)
        for i, src in enumerate(srcList):
            ep = src.ePrimary(self.survey.prob)
            if ep is not None:
                ePrimary[:,i] = ep[:,-1]
        return ePrimary

    def _eSecondary(self, eSolution, srcList):
        return eSolution

    def _e(self, eSolution, srcList):
        return self._ePrimary(eSolution,srcList) + self._eSecondary(eSolution,srcList)

    def _eDeriv_u(self, src, v, adjoint = False):
        return None

    def _eDeriv_m(self, src, v, adjoint = False):
        # assuming primary does not depend on the model
        return None

    def _bPrimary(self, eSolution, srcList):
        bPrimary = np.zeros([self.survey.mesh.nE,eSolution.shape[1]], dtype = complex)
        for i, src in enumerate(srcList):
            bp = src.bPrimary(self.survey.prob)
            if bp is not None:
                bPrimary[:,i] += bp[:,-1]
        return bPrimary

    def _bSecondary(self, eSolution, srcList):
        C = self.mesh.nodalGrad
        b = (C * eSolution)
        for i, src in enumerate(srcList):
            b[:,i] *= - 1./(1j*omega(src.freq))
            # There is no magnetic source in the MT problem
            # S_m, _ = src.eval(self.survey.prob)
            # if S_m is not None:
            #     b[:,i] += 1./(1j*omega(src.freq)) * S_m
        return b

    def _b(self, eSolution, srcList):
        return self._bPrimary(eSolution, srcList) + self._bSecondary(eSolution, srcList)

    def _bSecondaryDeriv_u(self, src, v, adjoint = False):
        C = self.mesh.nodalGrad
        if adjoint:
            return - 1./(1j*omega(src.freq)) * (C.T * v)
        return - 1./(1j*omega(src.freq)) * (C * v)

    def _bSecondaryDeriv_m(self, src, v, adjoint = False):
        S_mDeriv, _ = src.evalDeriv(self.survey.prob, adjoint)
        S_mDeriv = S_mDeriv(v)
        if S_mDeriv is not None:
            return 1./(1j * omega(src.freq)) * S_mDeriv
        return None

    def _bDeriv_u(self, src, v, adjoint=False):
        # Primary does not depend on u
        return self._bSecondaryDeriv_u(src, v, adjoint)

    def _bDeriv_m(self, src, v, adjoint=False):
        # Assuming the primary does not depend on the model
        return self._bSecondaryDeriv_m(src, v, adjoint)

