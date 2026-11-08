#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <mln/shaders/shader_program_base.hpp>
#include <mln/mtl/mtl_fwd.hpp>
#include <mln/mtl/vertex_attribute.hpp>

#include <Foundation/NSSharedPtr.hpp>

#include <optional>
#include <string>
#include <unordered_map>

namespace mln {
namespace shaders {

struct AttributeInfo {
    constexpr AttributeInfo(std::size_t index_,
                            gfx::AttributeDataType dataType_,
                            std::size_t bufferIndex_,
                            std::size_t id_)
        : index(index_),
          dataType(dataType_),
          bufferIndex(bufferIndex_),
          id(id_) {}
    std::size_t index;
    gfx::AttributeDataType dataType;
    std::size_t bufferIndex;
    std::size_t id;
};

struct TextureInfo {
    constexpr TextureInfo(std::size_t index_, std::size_t id_)
        : index(index_),
          id(id_) {}
    std::size_t index;
    std::size_t id;
};

} // namespace shaders
namespace mtl {
class RenderableResource;
class RendererBackend;
class ShaderProgram;
using UniqueShaderProgram = std::unique_ptr<ShaderProgram>;

class ShaderProgram final : public gfx::ShaderProgramBase {
public:
    /// Functions of a program whose Metal library is compiling off the render thread
    /// (gfx::Context::setAsyncShaderCompilation). Filled in by the compile completion handler;
    /// the program adopts them on the render thread once `ready` is set.
    struct PendingFunctions {
        std::mutex mutex;
        MTLFunctionPtr vertex;
        MTLFunctionPtr fragment;
        std::atomic<bool> ready{false};
        std::atomic<bool> failed{false};
        /// The context's count of work in flight off the render thread; the program's pipeline
        /// states are counted in it too, so the renderer keeps requesting frames until they land.
        std::shared_ptr<std::atomic<int>> counter;
    };

    /// A render pipeline state Metal is building off the render thread, filled in by the
    /// completion handler and adopted on the render thread once `done` is set.
    struct PendingPipeline {
        std::mutex mutex;
        MTLRenderPipelineStatePtr state;
        std::atomic<bool> done{false};
    };

    ShaderProgram(std::string name,
                  RendererBackend& backend,
                  MTLFunctionPtr vertexFunction,
                  MTLFunctionPtr fragmentFunction);
    /// A program still compiling: `getRenderPipelineState` returns null until the functions land.
    ShaderProgram(std::string name, RendererBackend& backend, std::shared_ptr<PendingFunctions> pending);
    ~ShaderProgram() noexcept override;

    static constexpr std::string_view Name{"GenericMTLShader"};
    const std::string_view typeName() const noexcept override { return Name; }

    /// Whether the compiled functions are available (always true for synchronously compiled
    /// programs). Drawables skip their draw while this is false; the renderer keeps requesting
    /// frames until it turns true.
    bool isReady() const;

    /// Whether the pipeline state for `reuseHash` is being built off the render thread
    /// (`getRenderPipelineState` returned null for it). A drawable skips its draw meanwhile.
    bool isPipelineStatePending(std::size_t reuseHash) const;

    MTLRenderPipelineStatePtr getRenderPipelineState(const gfx::Renderable&,
                                                     const MTLVertexDescriptorPtr&,
                                                     const gfx::ColorMode& colorMode,
                                                     const std::optional<std::size_t> reuseHash) const;

    std::optional<size_t> getSamplerLocation(const size_t id) const override;

    const gfx::VertexAttributeArray& getVertexAttributes() const override { return vertexAttributes; }

    const gfx::VertexAttributeArray& getInstanceAttributes() const override { return instanceAttributes; }

    void initVertexAttribute(const shaders::AttributeInfo&);
    void initInstanceAttribute(const shaders::AttributeInfo&);
    void initTexture(const shaders::TextureInfo&);

protected:
    MTLRenderPipelineDescriptorPtr makePipelineDescriptor(const gfx::Renderable&,
                                                          const MTLVertexDescriptorPtr&,
                                                          const gfx::ColorMode&) const;

    std::string shaderName;
    RendererBackend& backend;
    mutable MTLFunctionPtr vertexFunction;
    mutable MTLFunctionPtr fragmentFunction;
    mutable std::shared_ptr<PendingFunctions> pending;
    /// Set for a program compiled asynchronously: its pipeline states are built asynchronously
    /// too, because building one is where Metal compiles the program for the GPU, which on a
    /// cold shader cache blocks for longer than compiling the library did.
    bool asyncPipelineStates = false;
    std::shared_ptr<std::atomic<int>> pendingCounter;
    mutable mln::unordered_map<std::size_t, std::shared_ptr<PendingPipeline>> pendingPipelineStates;
    VertexAttributeArray vertexAttributes;
    VertexAttributeArray instanceAttributes;
    std::array<std::optional<size_t>, shaders::maxTextureCountPerShader> textureBindings;

    mutable mln::unordered_map<std::size_t, MTLRenderPipelineStatePtr> renderPipelineStateCache;
};

} // namespace mtl
} // namespace mln
