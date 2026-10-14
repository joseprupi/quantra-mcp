/* fixture: iterativebootstrap.hpp */
namespace QuantLib {

    template <class Curve>
    class IterativeBootstrap {
      public:
        void calculate() const;
    };

    template <class Curve>
    void IterativeBootstrap<Curve>::calculate() const {
        // bootstrap loop (fixture)
        for (Size iteration = 0; ; ++iteration) {
            if (iteration == maxIterations) break;
        }
    }

}
