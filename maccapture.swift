// Mac ekranını ve sistem sesini ScreenCaptureKit ile yakalar (sürücü gerekmez; Ekran Kaydı izni yeterli).
//
//   maccapture <genişlik> <yükseklik> <ses-fifo>
//
// stdout: ham görüntü, NV12, sabit 30 fps (ekran değişmese de son kare tekrarlanır)
// <ses-fifo>: ham ses, 48 kHz stereo float32 (f32le); ses yokken sessizlik yazılır
// İkisi de gerçek saate kilitli, böylece ffmpeg'de ses ve görüntü hizalı kalır.
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

let args = CommandLine.arguments
let width = args.count == 4 ? Int(args[1]) ?? 0 : 0
let height = args.count == 4 ? Int(args[2]) ?? 0 : 0
guard width > 0, height > 0 else {
    FileHandle.standardError.write("kullanım: maccapture <genişlik> <yükseklik> <ses-fifo>\n".data(using: .utf8)!)
    exit(2)
}
let fps = 30
let rate = 48000.0
let start = Date()
signal(SIGPIPE, SIG_DFL)  // okuyan taraf kapanınca sessizce çık

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("maccapture: \(msg)\n".data(using: .utf8)!)
    exit(1)
}

// MARK: Görüntü

let videoQueue = DispatchQueue(label: "video")
let videoOut = FileHandle.standardOutput
var latest = Data(repeating: 0, count: width * height) + Data(repeating: 128, count: width * height / 2)  // siyah
var framesWritten = 0

func copyNV12(_ pb: CVPixelBuffer) -> Data? {
    guard CVPixelBufferGetWidth(pb) == width, CVPixelBufferGetHeight(pb) == height,
          CVPixelBufferLockBaseAddress(pb, .readOnly) == kCVReturnSuccess else { return nil }
    defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
    var data = Data(capacity: width * height * 3 / 2)
    for (plane, rows) in [(0, height), (1, height / 2)] {
        guard let base = CVPixelBufferGetBaseAddressOfPlane(pb, plane) else { return nil }
        let stride = CVPixelBufferGetBytesPerRowOfPlane(pb, plane)
        for y in 0..<rows { data.append(base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self), count: width) }
    }
    return data
}

// MARK: Ses

let audioQueue = DispatchQueue(label: "audio")
var audioOut: FileHandle?
var samplesWritten = 0  // kare (frame) sayısı; sadece audioQueue üzerinde değişir

DispatchQueue.global().async {  // fifo, ffmpeg okumaya başlayana kadar açılmaz; görüntüyü bekletmesin
    guard let h = FileHandle(forWritingAtPath: args[3]) else { fail("ses fifo açılamadı") }
    audioQueue.async { audioOut = h }
}

func writeAudio(_ samples: [Float]) {
    guard let out = audioOut else { return }
    // Gerçek saatin önüne geçtiysek (araya sessizlik dolduğu için) fazlasını at.
    let ahead = samplesWritten - Int(Date().timeIntervalSince(start) * rate)
    let drop = ahead > Int(rate / 20) ? min(ahead, samples.count / 2) : 0
    guard drop < samples.count / 2 else { return }
    samples[(drop * 2)...].withUnsafeBufferPointer { out.write(Data(buffer: $0)) }
    samplesWritten += samples.count / 2 - drop
}

// MARK: Yakalama

final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sb.isValid else { return }
        if type == .screen {
            if let pb = sb.imageBuffer, let frame = copyNV12(pb) { latest = frame }
            return
        }
        guard type == .audio else { return }
        try? sb.withAudioBufferList { abl, _ in
            if abl.count >= 2 {  // planar: sol ve sağ ayrı tamponlarda → interleave
                let n = Int(abl[0].mDataByteSize) / 4
                guard let l = abl[0].mData?.assumingMemoryBound(to: Float.self),
                      let r = abl[1].mData?.assumingMemoryBound(to: Float.self) else { return }
                var buf = [Float](repeating: 0, count: n * 2)
                for i in 0..<n { buf[2 * i] = l[i]; buf[2 * i + 1] = r[i] }
                writeAudio(buf)
            } else if let p = abl[0].mData?.assumingMemoryBound(to: Float.self) {
                let n = Int(abl[0].mDataByteSize) / 4
                writeAudio(abl[0].mNumberChannels == 2 ? Array(UnsafeBufferPointer(start: p, count: n))
                                                       : (0..<n).flatMap { [p[$0], p[$0]] })
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) { fail(error.localizedDescription) }
}

let capture = Capture()
var stream: SCStream?

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else { fail("ekran bulunamadı") }
        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        config.preservesAspectRatio = true  // 16:10 ekran → 16:9 karede siyah kenarlar
        config.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
        config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
        config.queueDepth = 4
        config.showsCursor = true
        config.capturesAudio = true
        config.sampleRate = Int(rate)
        config.channelCount = 2
        config.excludesCurrentProcessAudio = true
        let s = SCStream(filter: SCContentFilter(display: display, excludingWindows: []), configuration: config, delegate: capture)
        try s.addStreamOutput(capture, type: .screen, sampleHandlerQueue: videoQueue)
        try s.addStreamOutput(capture, type: .audio, sampleHandlerQueue: audioQueue)
        try await s.startCapture()
        stream = s
    } catch {
        fail(error.localizedDescription)
    }
}

// Saat: görüntüyü sabit 30 fps yaz, ses gelmiyorsa sessizlikle doldur.
let videoTimer = DispatchSource.makeTimerSource(queue: videoQueue)
videoTimer.schedule(deadline: .now(), repeating: .milliseconds(1000 / fps / 2))
videoTimer.setEventHandler {
    let due = Int(Date().timeIntervalSince(start) * Double(fps))
    while framesWritten < due {
        videoOut.write(latest)
        framesWritten += 1
    }
}
videoTimer.resume()

let audioTimer = DispatchSource.makeTimerSource(queue: audioQueue)
audioTimer.schedule(deadline: .now(), repeating: .milliseconds(20))
audioTimer.setEventHandler {
    let expected = Int(Date().timeIntervalSince(start) * rate)
    if audioOut != nil, expected - samplesWritten > Int(rate / 10) {
        writeAudio([Float](repeating: 0, count: (expected - samplesWritten) * 2))
    }
}
audioTimer.resume()
dispatchMain()
