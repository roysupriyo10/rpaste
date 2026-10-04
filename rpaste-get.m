// Read the GUI clipboard as PNG, including TIFF and copied image files.
#import <AppKit/AppKit.h>
#include <stdio.h>

static NSData *png_data(NSData *data) {
    if (!data) return nil;
    NSBitmapImageRep *bitmap = [NSBitmapImageRep imageRepWithData:data];
    return [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
}

int main(void) {
    @autoreleasepool {
        NSPasteboard *board = [NSPasteboard generalPasteboard];
        NSData *image = [board dataForType:NSPasteboardTypePNG];
        if (!image) image = png_data([board dataForType:NSPasteboardTypeTIFF]);
        if (!image) {
            NSArray *files = [board readObjectsForClasses:@[[NSURL class]]
                                                 options:@{NSPasteboardURLReadingFileURLsOnlyKey: @YES}];
            for (NSURL *url in files) {
                image = png_data([NSData dataWithContentsOfURL:url]);
                if (image) break;
            }
        }
        if (!image) {
            fputs("rpaste: no image in clipboard\n", stderr);
            return 1;
        }
        return fwrite(image.bytes, 1, image.length, stdout) == image.length ? 0 : 1;
    }
}
