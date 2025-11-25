# The Hardware Index (hwidx.org)

<p align="center">
  <p align="center"><img src="https://m.hwidx.org/assets/img/wellcome-logo-white.png" height="120px" alt="The Wellcome Trust Logo"></p>
  <p align="center"><a href="https://wellcome.org/">Funded by Wellcome</a></p>
</p>

An open index of stable, consistent, and shareable identifiers for hardware artifacts.
Each artifact is represented by a single YAML (.yml) file in this repository.

This repository is the canonical source for hwidx.org identifiers and is managed via GitHub pull requests.

## Features

- Single — One-to-one mapping between an artifact and its documentation  
- Stable — Once allocated, an identifier never changes and always refers to the same artifact  
- Open — Anyone may request an identifier; the index is freely available

## Repository layout

- Each artifact: one .yml file placed in the appropriate folder (folders generally follow schema/namespace rules).  
- Files normally follow a DOI-like metadata schema (title, identifiers, URL, creators, dates, notes).  
- CI may run basic schema/format checks on PRs — follow repository guidance in CONTRIBUTING.md (if present).

## How to request an identifier

1. Fork the repository.  
2. Add a YAML file with your artifact metadata in the correct folder. Use an informative filename and valid YAML.  
3. Open a pull request describing the artifact and intended identifier.

A reviewer will merge, request changes, or ask questions to meet identifier requirements.

## Example artifact (minimal)
```yaml
title: "Example Device Board v1"
url: "https://example.com/docs/example-device"
```

For other examples, browse the `1/` and `2/` organisation paths for our own devices.

Note that the only key that the router cares about is `url`, and any other data is currently used as free-form metadata. This may change however, and we are open to suggestions for which additional fields we should formalise in the standard.

## How to display an hwidx identifier on a device

Recommended: a QR code with a solid border and a short-form URL (no scheme) in the top or bottom border. This supports both QR scanning and manual entry.
Various logomarks and formats are automatically generated for each artifact, and can be found on the metapage for each. See https://hwidx.org/ for examples, or add `m.` before the URL of any hwidx URL to see the metapage for this artifact (ex. https://m.hwidx.org/2/1.1.0/ )

## How to use an hwidx identifier

- Human redirect: https://hwidx.org/<identifier> (e.g. hwidx.org/10.100/1234) - This will take the user to the address listed at the identifier's `url` field.
- Machine metadata: https://m.hwidx.org/<identifier> - This will return the metadata record / vCard-like info on m.hwidx.org for this identifier

## Contributing

- Use pull requests to add or update identifiers.
- Follow YAML schema and naming conventions used in the repository.
- Open issues for questions or non-PR requests.

## Links

- Project website: https://hwidx.org/
- Project repository: https://github.com/ResearchCulture/hwidx  
- DOI: https://www.doi.org/
