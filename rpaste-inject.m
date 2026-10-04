// rpaste-inject.m — DYLD_INSERT_LIBRARIES shim for NSPasteboard.
// Makes Bun.Image.fromClipboard() find rpaste's staged image.
// Compile: clang -shared -framework AppKit -o rpaste-inject.dylib rpaste-inject.m
#import <AppKit/AppKit.h>
#import <objc/runtime.h>

static NSString *rpaste_latest_path(void) {
    const char *images = getenv("RPASTE_IMAGES_DIR");
    if (images && *images) {
        return [[NSString stringWithUTF8String:images] stringByAppendingPathComponent:@"latest.png"];
    }
    const char *home = getenv("HOME");
    if (!home) return nil;
    NSString *xdg = nil;
    const char *xdg_state = getenv("XDG_STATE_HOME");
    if (xdg_state) {
        xdg = [NSString stringWithUTF8String:xdg_state];
    } else {
        xdg = [NSString stringWithFormat:@"%s/.local/state", home];
    }
    return [xdg stringByAppendingPathComponent:@"rpaste/images/latest.png"];
}

static IMP original_dataForType = NULL;

static NSData *swizzled_dataForType(id self, SEL _cmd, NSPasteboardType type) {
    // Scoped agent environments prefer their own staged image over an
    // unrelated GUI clipboard on the remote macOS machine.
    NSData *result = ((NSData *(*)(id, SEL, NSPasteboardType))original_dataForType)(self, _cmd, type);
    if (result && !getenv("RPASTE_IMAGES_DIR")) return result;

    // No data from real pasteboard — check if rpaste has a staged image
    if ([type isEqualToString:NSPasteboardTypePNG] ||
        [type isEqualToString:@"public.png"] ||
        [type isEqualToString:@"Apple PNG pasteboard type"]) {
        NSString *path = rpaste_latest_path();
        if (path && [[NSFileManager defaultManager] fileExistsAtPath:path]) {
            return [NSData dataWithContentsOfFile:path];
        }
    }
    if ([type isEqualToString:NSPasteboardTypeTIFF] ||
        [type isEqualToString:@"public.tiff"]) {
        NSString *path = rpaste_latest_path();
        if (path && [[NSFileManager defaultManager] fileExistsAtPath:path]) {
            NSData *png = [NSData dataWithContentsOfFile:path];
            if (png) {
                NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithData:png];
                if (rep) return [rep TIFFRepresentation];
            }
        }
    }
    return result;
}

static IMP original_types = NULL;
static IMP original_readObjects = NULL;

static NSArray *swizzled_readObjects(id self, SEL _cmd, NSArray *classes, NSDictionary *options) {
    // Codex prefers image files from NSURL clipboard entries before reading
    // TIFF bytes. A scoped staged image must not lose to a remote GUI file.
    if (getenv("RPASTE_IMAGES_DIR") && [classes containsObject:[NSURL class]]) {
        NSString *path = rpaste_latest_path();
        if (path && [[NSFileManager defaultManager] fileExistsAtPath:path]) return @[];
    }
    return ((NSArray *(*)(id, SEL, NSArray *, NSDictionary *))original_readObjects)(self, _cmd, classes, options);
}

static NSArray *swizzled_types(id self, SEL _cmd) {
    NSArray *result = ((NSArray *(*)(id, SEL))original_types)(self, _cmd);
    if (result && result.count > 0 && !getenv("RPASTE_IMAGES_DIR")) return result;

    // No types from real pasteboard — check rpaste
    NSString *path = rpaste_latest_path();
    if (path && [[NSFileManager defaultManager] fileExistsAtPath:path]) {
        return @[NSPasteboardTypePNG, NSPasteboardTypeTIFF];
    }
    return result;
}

__attribute__((constructor))
static void rpaste_inject_init(void) {
    Method m = class_getInstanceMethod([NSPasteboard class], @selector(dataForType:));
    if (m) {
        original_dataForType = method_getImplementation(m);
        method_setImplementation(m, (IMP)swizzled_dataForType);
    }
    Method t = class_getInstanceMethod([NSPasteboard class], @selector(types));
    if (t) {
        original_types = method_getImplementation(t);
        method_setImplementation(t, (IMP)swizzled_types);
    }
    Method r = class_getInstanceMethod([NSPasteboard class], @selector(readObjectsForClasses:options:));
    if (r) {
        original_readObjects = method_getImplementation(r);
        method_setImplementation(r, (IMP)swizzled_readObjects);
    }
}
