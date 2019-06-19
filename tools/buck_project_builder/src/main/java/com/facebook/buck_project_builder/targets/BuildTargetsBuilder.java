package com.facebook.buck_project_builder.targets;

import com.facebook.buck_project_builder.BuilderException;
import com.facebook.buck_project_builder.DebugOutput;
import com.facebook.buck_project_builder.FileSystem;
import com.google.common.collect.ImmutableSet;
import org.apache.commons.io.FileUtils;

import java.io.File;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.logging.Logger;

public final class BuildTargetsBuilder {

  private static final Logger LOGGER = Logger.getGlobal();

  private final String buckRoot;
  private final String outputDirectory;
  private final String temporaryThriftOutputDirectory;
  /** key: output path, value: source path */
  private final Map<Path, Path> sources = new HashMap<>();

  private final Set<String> unsupportedGeneratedSources = new HashSet<>();
  private final Set<String> pythonWheelUrls = new HashSet<>();
  private final Set<String> thriftLibraryBuildCommands = new HashSet<>();
  private final Set<String> swigLibraryBuildCommands = new HashSet<>();

  private final Set<String> conflictingFiles = new HashSet<>();
  private final Set<String> unsupportedFiles = new HashSet<>();

  public BuildTargetsBuilder(String buckRoot, String outputDirectory) throws BuilderException {
    this.buckRoot = buckRoot;
    this.outputDirectory = outputDirectory;
    try {
      this.temporaryThriftOutputDirectory = Files.createTempDirectory("thrift_gen_").toString();
    } catch (IOException exception) {
      throw new BuilderException("Cannot create temporary directory for thrift code generation.");
    }
  }

  private static void logCodeGenerationIOException(IOException exception) {
    LOGGER.warning("IOException during python code generation: " + exception.getMessage());
  }

  private void buildPythonSources() {
    this.sources
        .entrySet()
        .parallelStream()
        .forEach(mapping -> FileSystem.addSymbolicLink(mapping.getKey(), mapping.getValue()));
  }

  private void buildPythonWheels() {
    File outputDirectoryFile = new File(outputDirectory);
    this.pythonWheelUrls
        .parallelStream()
        .forEach(
            url -> {
              try {
                ImmutableSet<String> conflictingFiles =
                    FileSystem.unzipRemoteFile(url, outputDirectoryFile);
                this.conflictingFiles.addAll(conflictingFiles);
              } catch (IOException firstException) {
                try {
                  ImmutableSet<String> conflictingFiles =
                      FileSystem.unzipRemoteFile(url, outputDirectoryFile);
                  this.conflictingFiles.addAll(conflictingFiles);
                } catch (IOException secondException) {
                  LOGGER.warning(
                      String.format(
                          "Cannot fetch and unzip remote python dependency at `%s` after 1 retry.",
                          url));
                  LOGGER.warning("First IO Exception: " + firstException);
                  LOGGER.warning("Second IO Exception: " + secondException);
                }
              }
            });
  }

  private void buildThriftLibraries() {
    if (this.thriftLibraryBuildCommands.isEmpty()) {
      return;
    }
    thriftLibraryBuildCommands
        .parallelStream()
        .forEach(
            command -> {
              try {
                GeneratedBuildRuleRunner.runBuilderCommand(command, this.buckRoot);
              } catch (IOException exception) {
                logCodeGenerationIOException(exception);
              }
            });
    try {
      ImmutableSet<String> conflictingFiles;
      conflictingFiles =
          ThriftLibraryTarget.copyThriftStubs(
              Paths.get(this.temporaryThriftOutputDirectory, "gen-py"), this.outputDirectory);
      this.conflictingFiles.addAll(conflictingFiles);
      conflictingFiles =
          ThriftLibraryTarget.copyThriftStubs(
              Paths.get(this.temporaryThriftOutputDirectory, "gen-py3"), this.outputDirectory);
      this.conflictingFiles.addAll(conflictingFiles);
      FileUtils.deleteDirectory(new File(this.temporaryThriftOutputDirectory));
    } catch (IOException exception) {
      logCodeGenerationIOException(exception);
    }
  }

  private void buildSwigLibraries() {
    if (this.swigLibraryBuildCommands.isEmpty()) {
      return;
    }
    // Swig command contains buck run, so it's better not to make it run in parallel.
    for (String command : this.swigLibraryBuildCommands) {
      try {
        GeneratedBuildRuleRunner.runBuilderCommand(command, this.buckRoot);
      } catch (IOException exception) {
        logCodeGenerationIOException(exception);
      }
    }
  }

  private void generateEmptyStubs() {
    Path outputPath = Paths.get(outputDirectory);
    this.unsupportedGeneratedSources
        .parallelStream()
        .forEach(
            source -> {
              File outputFile = new File(source);
              if (outputFile.exists()) {
                // Do not generate stubs for files that has already been handled.
                return;
              }
              String relativeUnsupportedFilename =
                  outputPath.relativize(Paths.get(source)).normalize().toString();
              this.unsupportedFiles.add(relativeUnsupportedFilename);
              outputFile.getParentFile().mkdirs();
              try {
                FileUtils.write(outputFile, "# pyre-placeholder-stub\n", Charset.defaultCharset());
              } catch (IOException exception) {
                logCodeGenerationIOException(exception);
              }
            });
  }

  public String getBuckRoot() {
    return buckRoot;
  }

  public String getOutputDirectory() {
    return outputDirectory;
  }

  public String getTemporaryThriftOutputDirectory() {
    return temporaryThriftOutputDirectory;
  }

  /** Exposed for testing. */
  Map<Path, Path> getSources() {
    return sources;
  }

  /** Exposed for testing. */
  Set<String> getThriftLibraryBuildCommands() {
    return thriftLibraryBuildCommands;
  }

  /** Exposed for testing. */
  Set<String> getSwigLibraryBuildCommands() {
    return swigLibraryBuildCommands;
  }

  /** Exposed for testing. */
  Set<String> getUnsupportedGeneratedSources() {
    return unsupportedGeneratedSources;
  }

  /** Exposed for testing. */
  Set<String> getPythonWheelUrls() {
    return pythonWheelUrls;
  }

  void addSourceMapping(Path sourcePath, Path outputPath) {
    Path existingSourcePath = this.sources.get(outputPath);
    if (existingSourcePath != null && !existingSourcePath.equals(sourcePath)) {
      this.conflictingFiles.add(
          Paths.get(this.outputDirectory).relativize(outputPath).normalize().toString());
      return;
    }
    this.sources.put(outputPath, sourcePath);
  }

  void addUnsupportedGeneratedSource(String generatedSourcePath) {
    unsupportedGeneratedSources.add(generatedSourcePath);
  }

  void addPythonWheelUrl(String url) {
    pythonWheelUrls.add(url);
  }

  void addThriftLibraryBuildCommand(String command) {
    thriftLibraryBuildCommands.add(command);
  }

  void addSwigLibraryBuildCommand(String command) {
    swigLibraryBuildCommands.add(command);
  }

  public DebugOutput buildTargets() {
    this.buildPythonSources();
    this.buildPythonWheels();
    this.buildThriftLibraries();
    this.buildSwigLibraries();
    this.generateEmptyStubs();
    return new DebugOutput(this.conflictingFiles, this.unsupportedFiles);
  }
}
