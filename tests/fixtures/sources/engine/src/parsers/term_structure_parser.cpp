// fixture: a tiny stand-in for the engine's term structure parser
#include <ql/termstructures/yield/piecewiseyieldcurve.hpp>

namespace quantra {

std::shared_ptr<YieldTermStructure> buildCurve(const CurveSpec& spec) {
    // PiecewiseYieldCurve<Discount, LogLinear> is the default trait / interpolator
    auto curve = std::make_shared<PiecewiseYieldCurve<Discount, LogLinear>>(spec.referenceDate, helpers, dayCounter);
    return curve;
}

}  // namespace quantra
