import MapLibre
import XCTest

/// MLNMapView compiles shader libraries and builds render pipeline states off the
/// render thread: a drawable whose program or pipeline state is not ready skips its draw,
/// and the renderer keeps requesting frames until the work lands. These tests check the
/// second half of that contract: a style that needs several new programs and pipeline
/// states still reaches a fully rendered frame, rather than stalling with drawables that
/// never draw.
class MLNAsyncShaderCompilationTests: XCTestCase, MLNMapViewDelegate {
    var mapView: MLNMapView!
    var window: UIWindow!
    var styleURL: URL!
    var styleLoaded: XCTestExpectation?
    var fullyRendered: XCTestExpectation?
    var framesRendered = 0

    // Background, a fill with an outline and a line: several programs, each with its own
    // pipeline states, all new on the first frame.
    static let style = """
    {
      "version": 8,
      "sources": {
        "shapes": {
          "type": "geojson",
          "data": {
            "type": "FeatureCollection",
            "features": [
              {"type": "Feature", "properties": {},
               "geometry": {"type": "Polygon",
                            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]]}},
              {"type": "Feature", "properties": {},
               "geometry": {"type": "LineString", "coordinates": [[-20, -20], [20, 20]]}}
            ]
          }
        }
      },
      "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#ffffff"}},
        {"id": "fill", "type": "fill", "source": "shapes",
         "paint": {"fill-color": "#0f8a72", "fill-outline-color": "#000000"}},
        {"id": "line", "type": "line", "source": "shapes",
         "paint": {"line-color": "#ff0000", "line-width": 3}}
      ]
    }
    """

    override func setUp() {
        super.setUp()
        styleURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("async-shader-compilation-\(UUID().uuidString).json")
        XCTAssertNoThrow(try Self.style.write(to: styleURL, atomically: true, encoding: .utf8))

        mapView = MLNMapView(frame: CGRect(x: 0, y: 0, width: 256, height: 256), styleURL: styleURL)
        mapView.delegate = self
        mapView.setCenter(CLLocationCoordinate2D(latitude: 0, longitude: 0), zoomLevel: 2, animated: false)

        window = UIWindow(frame: mapView.bounds)
        window.addSubview(mapView)
        window.makeKeyAndVisible()
    }

    override func tearDown() {
        mapView = nil
        window = nil
        try? FileManager.default.removeItem(at: styleURL)
        super.tearDown()
    }

    func mapView(_: MLNMapView, didFinishLoading _: MLNStyle) {
        styleLoaded?.fulfill()
        styleLoaded = nil
    }

    func mapViewDidFinishRenderingFrame(_: MLNMapView, fullyRendered _: Bool) {
        framesRendered += 1
    }

    func mapViewDidFinishRenderingMap(_: MLNMapView, fullyRendered: Bool) {
        if fullyRendered {
            self.fullyRendered?.fulfill()
            self.fullyRendered = nil
        }
    }

    func testAStyleWithNewProgramsReachesAFullyRenderedFrame() {
        if mapView.style == nil {
            styleLoaded = expectation(description: "the style loads")
            wait(for: [styleLoaded!], timeout: 10)
        }
        fullyRendered = expectation(description: "the map finishes rendering, fully")
        mapView.setNeedsDisplay()
        // Generous: a cold shader cache on a simulator compiles each program in ~0.5 s,
        // concurrently. A renderer that stopped requesting frames while pipeline states were
        // pending would never get here.
        wait(for: [fullyRendered!], timeout: 30)
        XCTAssertGreaterThan(framesRendered, 0)
        XCTAssertEqual(mapView.style?.layer(withIdentifier: "fill")?.isVisible, true)
    }
}
