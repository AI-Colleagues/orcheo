// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OrcheoDesktop",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "OrcheoDesktop", targets: ["OrcheoDesktop"])
    ],
    targets: [
        .executableTarget(
            name: "OrcheoDesktop",
            path: "Sources/OrcheoDesktop"
        )
    ]
)
