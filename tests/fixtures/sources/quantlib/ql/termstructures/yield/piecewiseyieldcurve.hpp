/* fixture: piecewiseyieldcurve.hpp */
#include <ql/termstructures/iterativebootstrap.hpp>

namespace QuantLib {

    //! Piecewise yield term structure
    template <class Traits, class Interpolator, template <class> class Bootstrap = IterativeBootstrap>
    class PiecewiseYieldCurve : public Traits::template curve<Interpolator>::type, public LazyObject {
    };

}
