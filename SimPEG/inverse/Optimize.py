import numpy as np
import matplotlib.pyplot as plt
from SimPEG.utils import mkvc, sdiag
norm = np.linalg.norm
import scipy.sparse as sp

try:
    from pubsub import pub
    doPub = True
except Exception, e:
    print 'Warning: you may not have the required pubsub installed, use pypubsub. You will not be able to listen to events.'
    doPub = False



class Minimize(object):
    """

    Minimize is a general class for derivative based optimization.


    """

    name = "GeneralOptimizationAlgorithm"

    maxIter = 20
    maxIterLS = 10
    maxStep = np.inf
    LSreduction = 1e-4
    LSshorten = 0.5
    tolF = 1e-1
    tolX = 1e-1
    tolG = 1e-1
    eps = 1e-5

    def __init__(self, **kwargs):
        self._id = int(np.random.rand()*1e6)  # create a unique identifier to this program to be used in pubsub
        self.setKwargs(**kwargs)

    def setKwargs(self, **kwargs):
        # Set the variables, throw an error if they don't exist.
        for attr in kwargs:
            if hasattr(self, attr):
                setattr(self, attr, kwargs[attr])
            else:
                raise Exception('%s attr is not recognized' % attr)

    def minimize(self, evalFunction, x0):
        """

        evalFunction is a function handle::

            evalFunction(x, return_g=True, return_H=True )

        """
        self.evalFunction = evalFunction
        self.startup(x0)
        self.printInit()

        while True:
            self.f, self.g, self.H = evalFunction(self.xc, return_g=True, return_H=True)
            if doPub: pub.sendMessage('Minimize.evalFunction', minimize=self, f=self.f, g=self.g, H=self.H)
            self.printIter()
            if self.stoppingCriteria(): break
            p = self.findSearchDirection()
            if doPub: pub.sendMessage('Minimize.searchDirection', minimize=self, p=p)
            p = self.scaleSearchDirection(p)
            if doPub: pub.sendMessage('Minimize.scaleSearchDirection', minimize=self, p=p)
            xt, passLS = self.modifySearchDirection(p)
            if doPub: pub.sendMessage('Minimize.modifySearchDirection', minimize=self, xt=xt)
            if not passLS:
                xt, caught = self.modifySearchDirectionBreak(p)
                if not caught: return self.xc
            self.doEndIteration(xt)
            if doPub: pub.sendMessage('Minimize.endIteration', minimize=self, xt=xt)

        self.printDone()

        return self.xc

    @property
    def parent(self):
        """
            This is the parent of the optimization routine.
        """
        return getattr(self, '_parent', None)
    @parent.setter
    def parent(self, value):
        self._parent = value

    def startup(self, x0):
        self._iter = 0
        self._iterLS = 0
        self._STOP = np.zeros((5,1),dtype=bool)

        self.x0 = x0
        self.xc = x0
        self.xOld = x0

    def printInit(self):
        """
            printIter is called at the beginning of the optimization routine.

        """
        if doPub: pub.sendMessage('Minimize.printInit', minimize=self)
        if self.parent is not None and hasattr(self.parent, 'printInit'):
            self.parent.printInit()
            return
        print "%s %s %s" % ('='*22, self.name, '='*22)
        print "iter\tJc\t\tnorm(dJ)\tLS"
        print "%s" % '-'*57

    def printIter(self):
        """
            printIter is called directly after function evaluations.

        """
        if doPub: pub.sendMessage('Minimize.printIter', minimize=self)
        if self.parent is not None and hasattr(self.parent, 'printIter'):
            self.parent.printIter()
            return
        print "%3d\t%1.2e\t%1.2e\t%d" % (self._iter, self.f, norm(self.g), self._iterLS)

    def printDone(self):
        if doPub: pub.sendMessage('Minimize.printDone', minimize=self)
        if self.parent is not None and hasattr(self.parent, 'printDone'):
            self.parent.printDone()
            return
        print "%s STOP! %s" % ('-'*25,'-'*25)
        # TODO: put controls on gradient value, min model update, and function value
        if self._iter > 0:
            print "%d : |fc-fOld| = %1.4e <= tolF*(1+|fStop|) = %1.4e"  % (self._STOP[0], abs(self.f-self.fOld), self.tolF*(1+abs(self.fStop)))
            print "%d : |xc-xOld| = %1.4e <= tolX*(1+|x0|)    = %1.4e"  % (self._STOP[1], norm(self.xc-self.xOld), self.tolX*(1+norm(self.x0)))
        print "%d : |g|       = %1.4e <= tolG*(1+|fStop|) = %1.4e"  % (self._STOP[2], norm(self.g), self.tolG*(1+abs(self.fStop)))
        print "%d : |g|       = %1.4e <= 1e3*eps          = %1.4e"  % (self._STOP[3], norm(self.g), 1e3*self.eps)
        print "%d : iter      = %3d\t <= maxIter\t       = %3d"     % (self._STOP[4], self._iter, self.maxIter)
        print "%s DONE! %s\n" % ('='*25,'='*25)

    def stoppingCriteria(self):
        if self._iter == 0:
            self.fStop = self.f  # Save this for stopping criteria

        # check stopping rules
        self._STOP[0] = self._iter > 0 and (abs(self.f-self.fOld) <= self.tolF*(1+abs(self.fStop)))
        self._STOP[1] = self._iter > 0 and (norm(self.xc-self.xOld) <= self.tolX*(1+norm(self.x0)))
        self._STOP[2] = norm(self.g) <= self.tolG*(1+abs(self.fStop))
        self._STOP[3] = norm(self.g) <= 1e3*self.eps
        self._STOP[4] = self._iter >= self.maxIter
        return all(self._STOP[0:3]) | any(self._STOP[3:])

    def projection(self, p):
        return p

    def findSearchDirection(self):
        return -self.g

    def scaleSearchDirection(self, p):
        if self.maxStep < np.abs(p.max()):
            p = self.maxStep*p/np.abs(p.max())
        return p

    def modifySearchDirection(self, p):
        # Armijo linesearch
        descent = np.inner(self.g, p)
        t = 1
        iterLS = 0
        while iterLS < self.maxIterLS:
            xt = self.projection(self.xc + t*p)
            ft = self.evalFunction(xt, return_g=False, return_H=False)
            if ft < self.f + t*self.LSreduction*descent:
                break
            iterLS += 1
            t = self.LSshorten*t

        self._iterLS = iterLS
        return xt, iterLS < self.maxIterLS

    def modifySearchDirectionBreak(self, p):
        """
            Code is called if modifySearchDirection fails
            to find a descent direction.

            The search direction is passed as input and
            this function must pass back both a new searchDirection,
            and if the searchDirection break has been caught.

            By default, no additional work is done, and the
            function returns a False indicating the break was not caught.

            :param numpy.ndarray p: searchDirection
            :rtype: numpy.ndarray,bool
            :return: (xt, breakCaught)
        """
        print 'The linesearch got broken. Boo.'
        return p, False

    def doEndIteration(self, xt):
        # store old values
        self.fOld = self.f
        self.xOld, self.xc = self.xc, xt
        self._iter += 1


class GaussNewton(Minimize):
    name = 'GaussNewton'
    def findSearchDirection(self):
        return np.linalg.solve(self.H,-self.g)


class InexactGaussNewton(Minimize):
    name = 'InexactGaussNewton'
    def findSearchDirection(self):
        # TODO: use BFGS as a preconditioner or gauss sidel of the WtW or solve WtW directly
        p, info = sp.linalg.cg(self.H, -self.g, tol=1e-05, maxiter=10)
        return p


class SteepestDescent(Minimize):
    name = 'SteepestDescent'
    def findSearchDirection(self):
        return -self.g

if __name__ == '__main__':
    from SimPEG.tests import Rosenbrock, checkDerivative
    import matplotlib.pyplot as plt
    x0 = np.array([2.6, 3.7])
    checkDerivative(Rosenbrock, x0, plotIt=False)

    def listener1(minimize,p):
        print 'hi:  ', p
    if doPub: pub.subscribe(listener1, 'Minimize.searchDirection')

    xOpt = GaussNewton(maxIter=20,tolF=1e-10,tolX=1e-10,tolG=1e-10).minimize(Rosenbrock,x0)
    print "xOpt=[%f, %f]" % (xOpt[0], xOpt[1])
    xOpt = SteepestDescent(maxIter=30, maxIterLS=15,tolF=1e-10,tolX=1e-10,tolG=1e-10).minimize(Rosenbrock, x0)
    print "xOpt=[%f, %f]" % (xOpt[0], xOpt[1])

    def simplePass(x):
        return np.sin(x), sdiag(np.cos(x))

    def simpleFail(x):
        return np.sin(x), -sdiag(np.cos(x))

    checkDerivative(simplePass, np.random.randn(5), plotIt=False)
    checkDerivative(simpleFail, np.random.randn(5), plotIt=False)
