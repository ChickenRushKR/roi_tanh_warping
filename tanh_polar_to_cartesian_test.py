import os
import cv2
import torch
import numpy as np
from argparse import ArgumentParser
from ibug.face_detection import RetinaFacePredictor

from ibug.roi_tanh_warping import *
from ibug.roi_tanh_warping import reference_impl as ref


def test_pytorch_impl(frame, face_box, target_size, offset, restore, compare,
                      compare_direct, keep_aspect_ratio, reverse):
    # Preparation
    frames = torch.from_numpy(frame.astype(np.float32)).to(torch.device('cuda:0')).permute(2, 0, 1).unsqueeze(0)
    face_boxes = torch.from_numpy(np.array(face_box[:4], dtype=np.float32)).to(frames.device).unsqueeze(0)

    if reverse:
        # ROI-tanh warping
        roi_tanh_frames = roi_tanh_warp(frames, face_boxes, target_size, offset, padding='border')

        # ROI-tanh to ROI-tanh-polar
        roi_tanh_polar_frames = roi_tanh_to_roi_tanh_polar(roi_tanh_frames, face_boxes, padding='border',
                                                           keep_aspect_ratio=keep_aspect_ratio)

        # Restore from ROI-tanh-polar
        if restore:
            restored_frames = roi_tanh_polar_restore(roi_tanh_polar_frames, face_boxes, frame.shape[:2], offset,
                                                     padding='border', keep_aspect_ratio=keep_aspect_ratio)
        else:
            restored_frames = None

        # Compute difference with direct warping
        if compare_direct:
            reference_frames = roi_tanh_polar_warp(frames, face_boxes, target_size, offset, padding='border',
                                                   keep_aspect_ratio=keep_aspect_ratio)
            diff_directs = torch.abs(reference_frames - roi_tanh_polar_frames)
        else:
            diff_directs = None
    else:
        # ROI-tanh-polar warping
        roi_tanh_polar_frames = roi_tanh_polar_warp(frames, face_boxes, target_size, offset, padding='border',
                                                    keep_aspect_ratio=keep_aspect_ratio)

        # ROI-tanh-polar to ROI-tanh
        roi_tanh_frames = roi_tanh_polar_to_roi_tanh(roi_tanh_polar_frames, face_boxes, padding='border',
                                                     keep_aspect_ratio=keep_aspect_ratio)

        # Restore from ROI-tanh
        if restore:
            restored_frames = roi_tanh_restore(roi_tanh_frames, face_boxes, frame.shape[:2], offset, padding='border')
        else:
            restored_frames = None

        # Compute difference with direct warping
        if compare_direct:
            reference_frames = roi_tanh_warp(frames, face_boxes, target_size, offset, padding='border')
            diff_directs = torch.abs(reference_frames - roi_tanh_frames)
        else:
            diff_directs = None

    roi_tanh_polar_frame = roi_tanh_polar_frames[0].detach().permute(1, 2, 0).cpu().numpy().astype(np.uint8)
    roi_tanh_frame = roi_tanh_frames[0].detach().permute(1, 2, 0).cpu().numpy().astype(np.uint8)
    if restored_frames is None:
        restored_frame = None
    else:
        restored_frame = restored_frames[0].detach().permute(1, 2, 0).cpu().numpy().astype(np.uint8)
    if diff_directs is None:
        diff_direct = None
    else:
        diff_direct = diff_directs[0].detach().permute(1, 2, 0).cpu().numpy().astype(np.uint8)
    if compare:
        if reverse:
            ref_roi_tanh_polar_frame = ref.roi_tanh_to_roi_tanh_polar(roi_tanh_frame, face_box, target_size,
                                                                      border_mode=cv2.BORDER_REPLICATE,
                                                                      keep_aspect_ratio=keep_aspect_ratio)
            diff_ref = np.abs(ref_roi_tanh_polar_frame.astype(int) - roi_tanh_polar_frame.astype(int)).astype(np.uint8)
        else:
            ref_roi_tanh_frame = ref.roi_tanh_polar_to_roi_tanh(roi_tanh_polar_frame, face_box, target_size,
                                                                border_mode=cv2.BORDER_REPLICATE,
                                                                keep_aspect_ratio=keep_aspect_ratio)
            diff_ref = np.abs(ref_roi_tanh_frame.astype(int) - roi_tanh_frame.astype(int)).astype(np.uint8)
    else:
        diff_ref = None
    return roi_tanh_polar_frame, roi_tanh_frame, restored_frame, diff_ref, diff_direct


def main():
    parser = ArgumentParser()
    parser.add_argument('--video', '-v', help='video source')
    parser.add_argument('--width', '-x', help='face width', type=int, default=256)
    parser.add_argument('--height', '-y', help='face height', type=int, default=256)
    parser.add_argument('--offset', '-o', help='angular offset, only used when polar>0', type=float, default=0.0)
    parser.add_argument('--restore', '-r', help='show restored frames',
                        action='store_true', default=False)
    parser.add_argument('--compare', '-c', help='compare with reference implementation',
                        action='store_true', default=False)
    parser.add_argument('--compare-direct', '-t', help='compare with directly warped frames',
                        action='store_true', default=False)
    parser.add_argument('--keep-aspect-ratio', '-k', help='Keep aspect ratio in tanh-polar or tanh-circular warping',
                        action='store_true', default=False)
    parser.add_argument('--reverse', '-i', help='perform computation in the reverse direction',
                        action='store_true', default=False)
    parser.add_argument('--device', '-d', help='Device to be used (default=cuda:0)', default='cuda:0')
    parser.add_argument('--benchmark', '-b', help='Enable benchmark mode for CUDNN',
                        action='store_true', default=False)
    args = parser.parse_args()

    # Make the models run a bit faster
    torch.backends.cudnn.benchmark = args.benchmark

    # Create face detector
    detector = RetinaFacePredictor(device=args.device, model=RetinaFacePredictor.get_model('mobilenet0.25'))
    print('RetinaFace detector created using mobilenet0.25 backbone.')

    # Open webcam
    if os.path.exists(args.video):
        vid = cv2.VideoCapture(args.video)
        print('Video file opened: %s.' % args.video)
    else:
        vid = cv2.VideoCapture(int(args.video))
        print('Webcam #%d opened.' % int(args.video))

    # Detect objects in the frames
    try:
        frame_number = 0
        script_name = os.path.splitext(os.path.basename(__file__))[0]
        print('Face detection started, press \'Q\' to quit.')
        while True:
            _, frame = vid.read()
            if frame is None:
                break
            else:
                # Face detection
                face_boxes = detector(frame, rgb=False)
                if len(face_boxes) > 0:
                    biggest_face_idx = int(np.argmax([(bbox[3] - bbox[1]) * (bbox[2] - bbox[0])
                                                      for bbox in face_boxes]))

                    # Test the warping functions
                    roi_tanh_polar_frame, roi_tanh_frame, restored_frame, diff_ref, diff_direct = test_pytorch_impl(
                        frame, face_boxes[biggest_face_idx], (args.height, args.width), args.offset / 180.0 * np.pi,
                        args.restore, args.compare, args.compare_direct, args.keep_aspect_ratio, args.reverse)

                    # Rendering
                    for idx, bbox in enumerate(face_boxes):
                        if idx == biggest_face_idx:
                            border_colour = (0, 0, 255)
                        else:
                            border_colour = (128, 128, 128)
                        cv2.rectangle(frame, (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3])),
                                      color=border_colour, thickness=2)
                else:
                    roi_tanh_polar_frame = None
                    roi_tanh_frame = None
                    restored_frame = None
                    diff_ref = None
                    diff_direct = None

                # Show the result
                print('Frame #%d: %d faces(s) detected.' % (frame_number, len(face_boxes)))
                cv2.imshow(script_name, frame)
                if args.reverse:
                    if roi_tanh_frame is None:
                        cv2.destroyWindow('ROI-Tanh')
                    else:
                        cv2.imshow('ROI-Tanh', roi_tanh_frame)
                    if roi_tanh_polar_frame is None:
                        cv2.destroyWindow('ROI-Tanh-Polar')
                    else:
                        cv2.imshow('ROI-Tanh-Polar', roi_tanh_polar_frame)
                else:
                    if roi_tanh_polar_frame is None:
                        cv2.destroyWindow('ROI-Tanh-Polar')
                    else:
                        cv2.imshow('ROI-Tanh-Polar', roi_tanh_polar_frame)
                    if roi_tanh_frame is None:
                        cv2.destroyWindow('ROI-Tanh')
                    else:
                        cv2.imshow('ROI-Tanh', roi_tanh_frame)
                if args.restore:
                    if restored_frame is None:
                        cv2.destroyWindow('Restored')
                    else:
                        cv2.imshow('Restored', restored_frame)
                if args.compare_direct:
                    if diff_direct is None:
                        cv2.destroyWindow('Diff-w-Direct')
                    else:
                        cv2.imshow('Diff-w-Direct', diff_direct)
                if args.compare:
                    if diff_ref is None:
                        cv2.destroyWindow('Diff-w-Ref')
                    else:
                        cv2.imshow('Diff-w-Ref', diff_ref)
                key = cv2.waitKey(1) % 2 ** 16
                if key == ord('q') or key == ord('Q'):
                    print("\'Q\' pressed, we are done here.")
                    break
                else:
                    frame_number += 1
    finally:
        cv2.destroyAllWindows()
        vid.release()
        print('We are done here.')


if __name__ == '__main__':
    main()
