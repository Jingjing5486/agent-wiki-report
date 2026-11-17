import AppKit
import Foundation
import PDFKit
import Vision

struct OCRLine: Encodable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRPage: Encodable {
    let page: Int
    let status: String
    let text: String
    let meanConfidence: Double?
    let lines: [OCRLine]
    let error: String?
}

func emit(_ page: OCRPage) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    if let data = try? encoder.encode(page), let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

guard CommandLine.arguments.count >= 3 else {
    fputs("usage: macos_vision_ocr <file.pdf> <page[,page|start-end...]> [scale]\n", stderr)
    exit(2)
}

let pdfURL = URL(fileURLWithPath: CommandLine.arguments[1])
let pageSpec = CommandLine.arguments[2]
let scale = CommandLine.arguments.count > 3 ? (Double(CommandLine.arguments[3]) ?? 2.2) : 2.2
guard let document = PDFDocument(url: pdfURL) else {
    fputs("cannot open PDF: \(pdfURL.path)\n", stderr)
    exit(2)
}

var selected = Set<Int>()
for part in pageSpec.split(separator: ",") {
    let bounds = part.split(separator: "-").compactMap { Int($0) }
    if bounds.count == 1 {
        selected.insert(bounds[0])
    } else if bounds.count == 2, bounds[0] <= bounds[1] {
        for page in bounds[0]...bounds[1] { selected.insert(page) }
    } else {
        fputs("invalid page range: \(part)\n", stderr)
        exit(2)
    }
}

let supported = (try? VNRecognizeTextRequest().supportedRecognitionLanguages()) ?? []
let requestedLanguages = ["zh-Hans", "en-US"]
let languages = requestedLanguages.filter { supported.contains($0) }
fputs("Vision OCR languages: \(languages.isEmpty ? "system default" : languages.joined(separator: ", "))\n", stderr)

for pageNumber in selected.sorted() {
    guard (1...document.pageCount).contains(pageNumber),
          let page = document.page(at: pageNumber - 1) else {
        emit(OCRPage(page: pageNumber, status: "page_out_of_range", text: "",
                     meanConfidence: nil, lines: [], error: "page outside PDF"))
        continue
    }

    let bounds = page.bounds(for: .mediaBox)
    let size = CGSize(width: max(1, bounds.width * scale), height: max(1, bounds.height * scale))
    let pixelWidth = max(1, Int(size.width.rounded()))
    let pixelHeight = max(1, Int(size.height.rounded()))
    guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil, width: pixelWidth, height: pixelHeight,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: colorSpace,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        emit(OCRPage(page: pageNumber, status: "render_failed", text: "",
                     meanConfidence: nil, lines: [], error: "could not create PDF page render context"))
        continue
    }
    context.setFillColor(NSColor.white.cgColor)
    context.fill(CGRect(x: 0, y: 0, width: pixelWidth, height: pixelHeight))
    context.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: context)
    guard let cgImage = context.makeImage() else {
        emit(OCRPage(page: pageNumber, status: "render_failed", text: "",
                     meanConfidence: nil, lines: [], error: "could not create rendered CGImage"))
        continue
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .fast
    request.usesLanguageCorrection = false
    request.usesCPUOnly = true
    if !languages.isEmpty { request.recognitionLanguages = languages }
    request.minimumTextHeight = 0.003

    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
        let observations = request.results ?? []
        let lines = observations.compactMap { observation -> OCRLine? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return OCRLine(text: candidate.string, confidence: candidate.confidence,
                           x: box.origin.x, y: box.origin.y,
                           width: box.width, height: box.height)
        }
        let confidences = lines.map { Double($0.confidence) }
        let mean = confidences.isEmpty ? nil : confidences.reduce(0, +) / Double(confidences.count)
        emit(OCRPage(page: pageNumber, status: "ok", text: lines.map(\.text).joined(separator: "\n"),
                     meanConfidence: mean, lines: lines, error: nil))
    } catch {
        let nsError = error as NSError
        emit(OCRPage(page: pageNumber, status: "ocr_failed", text: "",
                     meanConfidence: nil, lines: [], error: "\(nsError.domain) [\(nsError.code)]: \(nsError.localizedDescription) \(nsError.userInfo)"))
    }
}
